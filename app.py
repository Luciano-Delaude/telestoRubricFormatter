import io
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import streamlit as st


@dataclass
class RubricItem:
    code: str
    description: str
    max_points: float
    details: str


@dataclass
class RangeBlock:
    start_code: str
    end_code: str
    header: str
    details: str


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def rtf_to_text(rtf: str) -> str:
    # Basic RTF to text converter sufficient for rubric parsing.
    # Replace paragraph/line breaks with newlines.
    text = re.sub(r"\\\r?\n", "\n", rtf)
    text = re.sub(r"\\par[d]?|\\line", "\n", text)

    # Handle unicode escapes like \uNNNN?
    def _unicode_repl(match: re.Match) -> str:
        num = int(match.group(1))
        return chr(num)

    text = re.sub(r"\\u(-?\d+)\??", _unicode_repl, text)

    # Handle hex escapes like \'97
    def _hex_repl(match: re.Match) -> str:
        return bytes.fromhex(match.group(1)).decode("cp1252", errors="ignore")

    text = re.sub(r"\\'([0-9a-fA-F]{2})", _hex_repl, text)

    # Drop all other control words and groups.
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"[{}]", "", text)

    # Normalize whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_rubric_items(
    text: str,
) -> Tuple[
    List[RubricItem],
    Dict[str, str],
    List[RangeBlock],
    Dict[str, Tuple[List[str], str]],
    str,
]:
    items: List[RubricItem] = []
    sections: Dict[str, str] = {}
    range_blocks: List[RangeBlock] = []
    inherited_scoring: Dict[str, Tuple[List[str], str]] = {}
    item_pattern = re.compile(
        r"^([A-Z]\d{1,3}|\d+(?:\.\d+)?)\)\s*(.+?)\s*\((\d+(?:\.\d+)?)(?:\s*points?)?\)\s*$"
    )
    section_pattern = re.compile(r"^([A-Z])\)\s*(.+)$")
    range_pattern = re.compile(
        r"^([A-Z]?\d{1,3}(?:\.\d{1,3})?)\s*[–-]\s*([A-Z]?\d{1,3}(?:\.\d{1,3})?)\)\s*(.+)$"
    )

    lines = [line.rstrip() for line in text.splitlines()]
    idx = 0
    preamble_lines: List[str] = []
    seen_any_item = False
    pending_group_lines: List[str] = []
    pending_group_has_scores = False
    active_group_bullets: List[str] = []
    active_group_details = ""
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue

        section_match = section_pattern.match(line)
        if section_match and not item_pattern.match(line):
            section_code, section_title = section_match.groups()
            sections[section_code] = section_title.strip()
            idx += 1
            seen_any_item = True
            pending_group_lines = []
            pending_group_has_scores = False
            active_group_bullets = []
            active_group_details = ""
            continue

        range_match = range_pattern.match(line)
        if range_match:
            start_code, end_code, title = range_match.groups()
            if re.match(r"^[A-Z]", start_code) and not re.match(r"^[A-Z]", end_code):
                end_code = f"{start_code[0]}{end_code}"
            header = f"{start_code}–{end_code}) {title.strip()}"
            detail_lines: List[str] = []
            idx += 1
            seen_any_item = True
            while idx < len(lines):
                peek = lines[idx].strip()
                if not peek:
                    idx += 1
                    if detail_lines and detail_lines[-1] != "":
                        detail_lines.append("")
                    continue
                if (
                    item_pattern.match(peek)
                    or section_pattern.match(peek)
                    or range_pattern.match(peek)
                ):
                    break
                detail_lines.append(peek)
                idx += 1
            details = "\n".join([line for line in detail_lines if line != ""]).strip()
            range_blocks.append(
                RangeBlock(
                    start_code=start_code,
                    end_code=end_code,
                    header=header,
                    details=details,
                )
            )
            pending_group_lines = []
            pending_group_has_scores = False
            active_group_bullets = []
            active_group_details = ""
            continue

        item_match = item_pattern.match(line)
        if not item_match:
            if not seen_any_item:
                preamble_lines.append(line)
            else:
                pending_group_lines.append(line)
                if re.match(r"^-?\s*\d+(?:\.\d+)?\s*:\s*", line.strip()):
                    pending_group_has_scores = True
            idx += 1
            continue

        code, description, max_points = item_match.groups()
        seen_any_item = True
        if pending_group_has_scores:
            group_text = "\n".join(pending_group_lines).strip()
            active_group_bullets, active_group_details = _extract_score_bullets(
                group_text
            )
            pending_group_lines = []
            pending_group_has_scores = False
        detail_lines: List[str] = []
        idx += 1
        while idx < len(lines):
            peek = lines[idx].strip()
            if not peek:
                idx += 1
                if detail_lines and detail_lines[-1] != "":
                    detail_lines.append("")
                continue
            if item_pattern.match(peek) or section_pattern.match(peek):
                break
            detail_lines.append(peek)
            idx += 1

        details = "\n".join([line for line in detail_lines if line != ""]).strip()
        items.append(
            RubricItem(
                code=code,
                description=description.strip(),
                max_points=float(max_points),
                details=details,
            )
        )
        if active_group_bullets or active_group_details:
            inherited_scoring[code] = (active_group_bullets, active_group_details)
    preamble = "\n".join(preamble_lines).strip()
    return items, sections, range_blocks, inherited_scoring, preamble


def load_rubric_from_upload(upload: io.BytesIO, name: str) -> List[RubricItem]:
    raw = upload.read()
    content = _decode_bytes(raw)
    if name.lower().endswith(".rtf"):
        content = rtf_to_text(content)
    items, _, _, _, _ = parse_rubric_items(content)
    return items


def _section_key(code: str) -> str:
    if re.match(r"^[A-Z]", code):
        return code[0]
    if "." in code:
        return code.split(".", 1)[0]
    return code


def group_items(items: List[RubricItem]) -> Dict[str, List[RubricItem]]:
    grouped: Dict[str, List[RubricItem]] = {}
    for item in items:
        section = _section_key(item.code)
        grouped.setdefault(section, []).append(item)
    return grouped


def format_output(
    items: List[RubricItem],
    scores: Dict[str, float],
    explanations: Dict[str, str],
) -> str:
    lines: List[str] = []
    total_obtained = sum(scores.get(item.code, 0.0) for item in items)
    total_possible = sum(item.max_points for item in items)
    lines.append(f"Total: {total_obtained:g}/{total_possible:g}")
    lines.append("")
    for item in items:
        score = scores.get(item.code, 0.0)
        explanation = explanations.get(item.code, "").strip()
        score_display = f"{score:g}/{item.max_points:g}"
        lines.append(f"{item.code}: {score_display}")
        lines.append(f"Explanation: {explanation}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


st.set_page_config(page_title="Rubric Rater", layout="wide")
st.title("Rubric Rater")
st.caption(
    "Upload a rubric .rtf or .txt to generate a structured rating form with optional justifications."
)

col_left, col_right = st.columns([2, 1])

with col_left:
    upload = st.file_uploader("Upload rubric file", type=["rtf", "txt"])
    use_sample = st.checkbox("Use bundled RubricTest.rtf", value=False)

items: List[RubricItem] = []
sections: Dict[str, str] = {}
range_blocks: List[RangeBlock] = []
inherited_scoring: Dict[str, Tuple[List[str], str]] = {}
raw_text: str = ""
preamble_text: str = ""
if use_sample and not upload:
    try:
        with open("RubricTest.rtf", "rb") as handle:
            content = _decode_bytes(handle.read())
            raw_text = rtf_to_text(content)
            (
                items,
                sections,
                range_blocks,
                inherited_scoring,
                preamble_text,
            ) = parse_rubric_items(raw_text)
    except FileNotFoundError:
        st.error("RubricTest.rtf not found in the app folder.")
elif upload:
    content = _decode_bytes(upload.read())
    if upload.name.lower().endswith(".rtf"):
        raw_text = rtf_to_text(content)
    else:
        raw_text = content
    items, sections, range_blocks, inherited_scoring, preamble_text = parse_rubric_items(
        raw_text
    )

if preamble_text:
    with st.expander("Rubric context (from file)"):
        st.text_area("Context", value=preamble_text, height=260)

if not items:
    st.info("No rubric items detected. Check the format or use the preview below.")
    if raw_text:
        with st.expander("Show extracted text preview"):
            st.text_area("Extracted text", value=raw_text[:4000], height=300)
    st.stop()

grouped = group_items(items)
scores: Dict[str, float] = {}
explanations: Dict[str, str] = {}

st.caption(f"Detected {len(items)} rubric items across {len(grouped)} sections.")
st.divider()

def _code_parts(code: str) -> Tuple[str, int]:
    if re.match(r"^[A-Z]", code):
        numeric = re.sub(r"\D", "", code[1:])
        return code[0], int(numeric or 0)
    if "." in code:
        section, rest = code.split(".", 1)
        numeric = re.sub(r"\D", "", rest)
        return section, int(numeric or 0)
    numeric = re.sub(r"\D", "", code)
    return numeric or code, 0


def _section_key(code: str) -> str:
    if re.match(r"^[A-Z]", code):
        return code[0]
    if "." in code:
        return code.split(".", 1)[0]
    return code


def _find_range_block(code: str) -> RangeBlock | None:
    letter, num = _code_parts(code)
    for block in range_blocks:
        start_letter, start_num = _code_parts(block.start_code)
        end_letter, end_num = _code_parts(block.end_code)
        if letter != start_letter or letter != end_letter:
            continue
        if start_num <= num <= end_num:
            return block
    return None


def _extract_score_bullets(text: str) -> Tuple[List[str], str]:
    if not text:
        return [], ""
    bullets: List[str] = []
    rest_lines: List[str] = []
    score_line = re.compile(r"^-?\s*(\d+(?:\.\d+)?)\s*:\s*(.+)$")
    for line in text.splitlines():
        match = score_line.match(line.strip())
        if match:
            bullets.append(f"{match.group(1)}: {match.group(2).strip()}")
        else:
            rest_lines.append(line)
    rest = "\n".join([line for line in rest_lines if line.strip()]).strip()
    return bullets, rest


for section, section_items in grouped.items():
    section_title = sections.get(section, "").strip()
    heading = f"Section {section}"
    if section_title:
        heading = f"{heading} — {section_title}"
    st.subheader(heading)
    rendered_ranges: set[str] = set()
    for item in section_items:
        block = _find_range_block(item.code)
        if block and block.header not in rendered_ranges:
            st.markdown(f"**{block.header}**")
            rendered_ranges.add(block.header)
        with st.container(border=True):
            st.markdown(f"**{item.code}** — {item.description}")
            item_bullets, item_details = _extract_score_bullets(item.details)
            inherited_bullets, inherited_details = inherited_scoring.get(
                item.code, ([], "")
            )
            range_bullets, range_details = ([], "")
            if block:
                range_bullets, range_details = _extract_score_bullets(block.details)

            display_bullets = item_bullets or range_bullets or inherited_bullets
            display_details = item_details or range_details or inherited_details

            if display_details:
                st.caption(display_details)
            if display_bullets:
                st.markdown("\n".join([f"- {bullet}" for bullet in display_bullets]))
            st.caption(f"Max points: {item.max_points:g}")
            all_points = st.radio(
                f"{item.code} rating mode",
                options=["All points", "Adjust score"],
                horizontal=True,
                key=f"mode_{item.code}",
            )
            if all_points == "All points":
                score = float(item.max_points)
                explanation = ""
            else:
                score = st.number_input(
                    f"{item.code} score",
                    min_value=0.0,
                    max_value=float(item.max_points),
                    value=float(item.max_points),
                    step=0.25,
                    key=f"score_{item.code}",
                )
                explanation = st.text_area(
                    f"{item.code} explanation",
                    placeholder="Add a short justification for deducted points.",
                    key=f"explain_{item.code}",
                )
            scores[item.code] = score
            explanations[item.code] = explanation

st.divider()
output_text = format_output(items, scores, explanations)
total_obtained = sum(scores.get(item.code, 0.0) for item in items)
total_possible = sum(item.max_points for item in items)
total_display = f"{total_obtained:g}/{total_possible:g}"

with col_right:
    st.subheader("Output")
    st.metric("Total score", total_display)
    st.text_area("Formatted output", value=output_text, height=400)
    st.download_button(
        "Download output",
        data=output_text,
        file_name="rubric_ratings.txt",
        mime="text/plain",
    )
