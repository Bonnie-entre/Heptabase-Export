import io
import json
import math
import re
import uuid
import zipfile
import copy
import base64
import binascii
import os
from urllib.parse import urlparse

import streamlit as st


CARD_REF_PATTERN = re.compile(
    r"{{card\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})}}"
)
ASSET_REF_PATTERN = re.compile(r"{{(?:image|img|file|asset)\s+([0-9a-zA-Z-]{8,})}}")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff"}
IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tiff",
}


st.title("Heptabase data to Obsidian Canvas")
st.markdown(
    "> This is a Web APP to export your Data from Heptabase to Obsidian, follow the instruction below to use."
)
st.markdown(
    """
            1. Upload your All-Data.json file. `All-Data.json` file is in your Heptabase export folder.
            2. After you upload the file, there will be two download buttons.
                1. Download Cards button will export all your cards in Heptabase to Markdown files in a folder with clean wiki link `[[]]`.
                2. Download Canvas button will export all your whiteboards in Heptabase to Obsidian Canvas file in a folder. You should set the Cards Path which is the cards' related path to your obsdian vault. The default path is `Cards/`
            """
)


def sanitize_filename(value, fallback="Untitled", max_len=80):
    text = str(value or "").strip()
    if not text:
        text = fallback

    text = re.sub(r"[\\/:*?\"<>|]", "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")

    if not text:
        text = fallback

    return text[:max_len].strip(" .") or fallback


def yaml_escape(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def get_display_title(card_title):
    if card_title and card_title.strip():
        return card_title.strip()
    return "Untitled"


def normalize_cards_path(path):
    result = str(path or "").strip().replace("\\", "/")
    if result and not result.endswith("/"):
        result += "/"
    return result


def normalize_local_path(path):
    return str(path or "").strip()


def normalize_uuid_key(value):
    return str(value or "").strip().lower()


def is_image_extension(value):
    ext = os.path.splitext(str(value or ""))[1].lower()
    return ext in IMAGE_EXTENSIONS


def extension_from_mime(mime_type):
    return IMAGE_MIME_EXT.get(str(mime_type or "").lower(), "")


def extension_from_path_or_url(value):
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    path = parsed.path if parsed.scheme else text
    return os.path.splitext(path)[1].lower()


def extract_base64_bytes(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return None

    if text.startswith("data:"):
        try:
            text = text.split(",", 1)[1]
        except IndexError:
            return None

    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def detect_image_asset(candidate):
    if not isinstance(candidate, dict):
        return None

    raw_type = str(candidate.get("type") or candidate.get("kind") or "").lower()
    mime_type = str(
        candidate.get("mimeType")
        or candidate.get("mime")
        or candidate.get("contentType")
        or ""
    ).lower()

    asset_id = (
        candidate.get("id")
        or candidate.get("uuid")
        or candidate.get("assetId")
        or candidate.get("fileId")
        or candidate.get("imageId")
        or candidate.get("resourceId")
    )
    if not asset_id:
        return None

    source_url = (
        candidate.get("url")
        or candidate.get("src")
        or candidate.get("downloadUrl")
        or candidate.get("publicUrl")
        or candidate.get("path")
        or candidate.get("relativePath")
        or candidate.get("filePath")
    )
    name = (
        candidate.get("fileName")
        or candidate.get("filename")
        or candidate.get("name")
        or candidate.get("title")
    )
    alt = candidate.get("alt") or candidate.get("caption") or candidate.get("title") or ""

    raw_base64 = (
        candidate.get("base64")
        or candidate.get("data")
        or candidate.get("contentBase64")
        or candidate.get("blob")
    )
    file_bytes = extract_base64_bytes(raw_base64)

    is_image = False
    if raw_type in {"image", "img", "photo"}:
        is_image = True
    elif mime_type.startswith("image/"):
        is_image = True
    elif is_image_extension(name) or is_image_extension(source_url):
        is_image = True

    if not is_image:
        return None

    return {
        "id": str(asset_id),
        "name": str(name or "Image"),
        "alt": str(alt or ""),
        "mime": mime_type,
        "source": str(source_url or ""),
        "bytes": file_bytes,
    }


def build_image_asset_index(all_data_json, local_asset_paths=None):
    asset_index = {}
    used_asset_names = set()
    local_asset_paths = local_asset_paths or {}

    def read_local_bytes(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    def register_asset(candidate):
        asset = detect_image_asset(candidate)
        if not asset:
            return

        asset_key = normalize_uuid_key(asset["id"])
        if not asset_key:
            return

        current = asset_index.get(asset_key)
        if current and current.get("bytes"):
            return

        local_candidates = []
        if asset["name"]:
            local_candidates = local_asset_paths.get(asset["name"], [])
        local_path = sorted(local_candidates)[0] if local_candidates else ""
        local_bytes = read_local_bytes(local_path) if local_path else None

        ext = (
            extension_from_mime(asset["mime"])
            or extension_from_path_or_url(asset["name"])
            or extension_from_path_or_url(asset["source"])
            or extension_from_path_or_url(local_path)
            or ".png"
        )
        stem = sanitize_filename(os.path.splitext(asset["name"])[0], fallback="Image")
        zip_name = build_unique_filename(stem, used_asset_names, extension=ext)
        zip_path = f"assets/{zip_name}"
        image_bytes = asset["bytes"] or local_bytes
        target = zip_path if image_bytes else asset["source"]

        if current:
            zip_path = current["zip_path"]
            current_target = current.get("target") or asset["source"]
            current_bytes = current.get("bytes")
            image_bytes = current_bytes or image_bytes
            target = zip_path if image_bytes else current_target

        asset_index[asset_key] = {
            "id": asset["id"],
            "alt": asset["alt"] or asset["name"],
            "zip_path": zip_path,
            "target": target,
            "bytes": image_bytes,
        }

    candidate_lists = [
        "assetList",
        "assets",
        "imageList",
        "images",
        "fileList",
        "files",
        "attachmentList",
        "attachments",
        "uploadList",
        "uploads",
        "mediaList",
        "resources",
    ]

    for key in candidate_lists:
        items = all_data_json.get(key)
        if isinstance(items, list):
            for item in items:
                register_asset(item)

    for card in all_data_json.get("cardList", []):
        if not isinstance(card, dict):
            continue
        for key in candidate_lists:
            items = card.get(key)
            if isinstance(items, list):
                for item in items:
                    register_asset(item)

    return asset_index


def build_local_asset_path_index(backup_root):
    index = {}
    root = normalize_local_path(backup_root)
    if not root or not os.path.isdir(root):
        return index

    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if not is_image_extension(filename):
                continue
            index.setdefault(filename, []).append(os.path.join(dirpath, filename))

    return index


def resolve_image_target(asset_index, asset_id=None, source=None):
    if asset_id:
        asset = asset_index.get(normalize_uuid_key(asset_id))
        if asset and asset.get("target"):
            return asset["target"], asset.get("alt", "")

    if source:
        source_text = str(source).strip()
        if source_text:
            return source_text, ""

    return "", ""


def render_image_markdown(target, alt_text):
    if not target:
        return ""
    if target.startswith("http://") or target.startswith("https://"):
        return f"![{alt_text}]({target})"
    # Use Obsidian embed syntax for local files to avoid path parsing issues.
    return f"![[{target}]]"


def build_unique_filename(stem, used_stems, extension=".md"):
    base = stem or "Untitled"
    candidate = base
    suffix = 2

    while candidate.lower() in used_stems:
        candidate = f"{base} ({suffix})"
        suffix += 1

    used_stems.add(candidate.lower())
    return f"{candidate}{extension}"


def normalize_node_type(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def get_node_children(node):
    if not isinstance(node, dict):
        return []
    for key in ("content", "children", "nodes"):
        value = node.get(key)
        if isinstance(value, list):
            return value
    return []


def get_mark_types_and_link(node):
    mark_types = set()
    link_href = ""

    if node.get("bold"):
        mark_types.add("bold")
    if node.get("italic"):
        mark_types.add("italic")
    if node.get("strikethrough"):
        mark_types.add("strikethrough")
    if node.get("code"):
        mark_types.add("code")

    marks = node.get("marks")
    if isinstance(marks, list):
        for mark in marks:
            if not isinstance(mark, dict):
                continue
            mark_type = normalize_node_type(mark.get("type") or mark.get("name"))
            attrs = mark.get("attrs") if isinstance(mark.get("attrs"), dict) else {}

            if mark_type in {"bold", "strong"}:
                mark_types.add("bold")
            elif mark_type in {"italic", "em"}:
                mark_types.add("italic")
            elif mark_type in {"strikethrough", "strike"}:
                mark_types.add("strikethrough")
            elif mark_type in {"code", "codespan"}:
                mark_types.add("code")
            elif mark_type == "link":
                link_href = mark.get("href") or attrs.get("href") or attrs.get("url") or link_href

    return mark_types, link_href


def apply_inline_format(text, node):
    if not text:
        return ""

    mark_types, link_href = get_mark_types_and_link(node)

    # Code span cannot reliably nest with other inline styles in Markdown.
    if "code" in mark_types:
        text = f"`{text}`"
    else:
        if "bold" in mark_types:
            text = f"**{text}**"
        if "italic" in mark_types:
            text = f"*{text}*"
        if "strikethrough" in mark_types:
            text = f"~~{text}~~"

    if link_href:
        return f"[{text}]({link_href})"

    return text


def render_inline(node, asset_index):
    if isinstance(node, str):
        return node

    if isinstance(node, list):
        return "".join(render_inline(item, asset_index) for item in node)

    if not isinstance(node, dict):
        return ""

    node_type = normalize_node_type(node.get("type"))
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}

    if node_type in {"image", "img", "photo"}:
        target, inferred_alt = resolve_image_target(
            asset_index,
            asset_id=attrs.get("fileId") or attrs.get("assetId") or attrs.get("id"),
            source=attrs.get("src") or attrs.get("url") or node.get("url"),
        )
        alt_text = attrs.get("alt") or attrs.get("title") or inferred_alt
        return render_image_markdown(target, alt_text)

    if node_type in {"hardbreak", "linebreak", "br"}:
        return "\n"

    if "text" in node and isinstance(node["text"], str):
        text = node["text"]
        return apply_inline_format(text, node)

    children = get_node_children(node)
    inner = "".join(render_inline(child, asset_index) for child in children)

    if node_type == "link":
        href = node.get("href") or node.get("url") or attrs.get("href") or attrs.get("url")
        if href:
            label = inner or href
            return f"[{label}]({href})"

    return apply_inline_format(inner, node)


def render_block(node, asset_index):
    if isinstance(node, str):
        return node

    if not isinstance(node, dict):
        return ""

    node_type = normalize_node_type(node.get("type", ""))
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    children = get_node_children(node)

    if node_type in {"heading", "header", "h1", "h2", "h3", "h4", "h5", "h6"} or node_type.startswith("heading"):
        level = node.get("level") or attrs.get("level") or 1
        if node_type in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(node_type[1])
        elif node_type.startswith("heading") and node_type != "heading":
            suffix = node_type.replace("heading", "")
            if suffix.isdigit():
                level = int(suffix)
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 1
        level = min(max(level, 1), 6)
        return f"{'#' * level} {render_inline(children, asset_index).strip()}"

    if node_type in {"image", "img", "photo"}:
        target, inferred_alt = resolve_image_target(
            asset_index,
            asset_id=attrs.get("fileId") or attrs.get("assetId") or attrs.get("id"),
            source=attrs.get("src") or attrs.get("url") or node.get("url"),
        )
        alt_text = attrs.get("alt") or attrs.get("title") or inferred_alt
        return render_image_markdown(target, alt_text)

    if node_type in {"blockquote", "quote"}:
        text = render_inline(children, asset_index).strip()
        return f"> {text}" if text else ""

    if node_type in {"codeblock", "code"}:
        language = node.get("language") or ""
        code = render_inline(children, asset_index)
        return f"```{language}\n{code}\n```"

    if node_type in {"bulletlist", "unorderedlist", "ul"}:
        lines = []
        for item in children:
            if isinstance(item, dict):
                item_text = render_inline(get_node_children(item), asset_index).strip()
            else:
                item_text = render_inline(item, asset_index).strip()
            if not item_text and isinstance(item, dict):
                item_text = render_inline(item, asset_index).strip()
            if item_text:
                lines.append(f"- {item_text}")
        return "\n".join(lines)

    if node_type in {"orderedlist", "ol"}:
        lines = []
        idx = 1
        for item in children:
            if isinstance(item, dict):
                item_text = render_inline(get_node_children(item), asset_index).strip()
            else:
                item_text = render_inline(item, asset_index).strip()
            if not item_text and isinstance(item, dict):
                item_text = render_inline(item, asset_index).strip()
            if item_text:
                lines.append(f"{idx}. {item_text}")
                idx += 1
        return "\n".join(lines)

    if node_type in {"horizontalrule", "hr"}:
        return "---"

    if node_type in {"paragraph", "p", "listitem", "li"}:
        return render_inline(children, asset_index).strip()

    text = render_inline(children, asset_index).strip()
    if text:
        return text

    return ""


def extract_plain_text(node):
    if isinstance(node, str):
        return node

    if isinstance(node, list):
        return "".join(extract_plain_text(item) for item in node)

    if not isinstance(node, dict):
        return ""

    text = ""
    if isinstance(node.get("text"), str):
        text += node["text"]

    for key in ("content", "children", "nodes"):
        children = node.get(key)
        if isinstance(children, list):
            text += "".join(extract_plain_text(child) for child in children)

    return text


def rich_text_to_markdown(content, asset_index):
    if content is None:
        return ""

    parsed = content

    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return content

    if isinstance(parsed, list):
        blocks = [render_block(node, asset_index).strip() for node in parsed]
    elif isinstance(parsed, dict):
        root_nodes = get_node_children(parsed)
        if normalize_node_type(parsed.get("type")) == "doc" and isinstance(root_nodes, list):
            blocks = [render_block(node, asset_index).strip() for node in root_nodes]
        elif isinstance(root_nodes, list):
            blocks = [render_block(node, asset_index).strip() for node in root_nodes]
        else:
            blocks = [render_block(parsed, asset_index).strip()]
    else:
        return str(parsed)

    blocks = [block for block in blocks if block]
    if blocks:
        return "\n\n".join(blocks)

    plain_text = extract_plain_text(parsed).strip()
    return plain_text


def replace_card_references(markdown_text, filename_by_id, title_by_id):
    id_map = {card_id.lower(): card_id for card_id in filename_by_id}

    def replacer(match):
        raw_id = match.group(1)
        card_id = id_map.get(raw_id.lower())
        if not card_id:
            return match.group(0)

        filename = filename_by_id[card_id]
        stem = filename[:-3] if filename.endswith(".md") else filename
        title = title_by_id[card_id]
        return f"[[{stem}|{title}]]"

    return CARD_REF_PATTERN.sub(replacer, markdown_text)


def replace_asset_references(markdown_text, asset_index):
    def replacer(match):
        asset_id = match.group(1)
        target, alt_text = resolve_image_target(asset_index, asset_id=asset_id)
        if not target:
            return match.group(0)
        return render_image_markdown(target, alt_text)

    return ASSET_REF_PATTERN.sub(replacer, markdown_text)


def build_card_index(card_list):
    filename_by_id = {}
    title_by_id = {}
    card_by_id = {}
    used_stems = set()

    for card in card_list:
        if card.get("isTrashed"):
            continue

        card_id = card.get("id")
        if not card_id:
            continue

        title = get_display_title(card.get("title"))
        safe_title = sanitize_filename(title)

        filename_by_id[card_id] = build_unique_filename(safe_title, used_stems)
        title_by_id[card_id] = title
        card_by_id[card_id] = card

    return card_by_id, filename_by_id, title_by_id


def build_card_markdown(card, card_id, filename_by_id, title_by_id, asset_index):
    display_title = title_by_id[card_id]
    raw_title = card.get("title", "")
    frontmatter = (
        "---\n"
        f'heptabase_id: "{yaml_escape(card_id)}"\n'
        f'heptabase_title: "{yaml_escape(raw_title)}"\n'
        f'heptabase_display_title: "{yaml_escape(display_title)}"\n'
        "---\n\n"
    )

    markdown_body = rich_text_to_markdown(card.get("content", ""), asset_index)
    markdown_body = replace_card_references(markdown_body, filename_by_id, title_by_id)
    markdown_body = replace_asset_references(markdown_body, asset_index)

    if markdown_body.strip():
        return frontmatter + markdown_body.strip() + "\n"

    return frontmatter


def detect_direction(begin, end):
    x_diff = begin.get("x", 0) - end.get("x", 0)
    y_diff = begin.get("y", 0) - end.get("y", 0)

    angle_deg = math.degrees(math.atan2(y_diff, x_diff))
    if 45 < angle_deg < 135:
        return ("bottom", "top")
    if angle_deg > 135 or angle_deg < -135:
        return ("left", "right")
    if -135 < angle_deg < -45:
        return ("top", "bottom")
    return ("right", "left")


def create_canvas(whiteboard, filename_by_id, cards_path):
    result = {"nodes": [], "edges": []}
    instance_to_node = {}

    for node in whiteboard.get("nodes", []):
        instance_id = node.get("id")
        if not instance_id:
            continue

        card_id = node.get("cardId")
        filename = filename_by_id.get(card_id)
        if not filename:
            continue

        canvas_node_id = uuid.uuid4().hex[:16]
        instance_to_node[instance_id] = canvas_node_id

        result["nodes"].append(
            {
                "id": canvas_node_id,
                "x": node.get("x", 0),
                "y": node.get("y", 0),
                "width": node.get("width", 400),
                "height": max(1, node.get("height", 300) - 30),
                "type": "file",
                "file": cards_path + filename,
            }
        )

    for section in whiteboard.get("sections", []):
        result["nodes"].append(
            {
                "id": uuid.uuid4().hex[:16],
                "x": section.get("x", 0),
                "y": section.get("y", 0),
                "width": section.get("width", 400),
                "height": section.get("height", 200),
                "type": "group",
                "label": section.get("title", ""),
            }
        )

    instances_by_id = {instance["id"]: instance for instance in whiteboard.get("nodes", []) if instance.get("id")}

    for connection in whiteboard.get("edges", []):
        if connection.get("beginObjectType") != "cardInstance" or connection.get("endObjectType") != "cardInstance":
            continue

        from_node = instance_to_node.get(connection.get("beginId"))
        to_node = instance_to_node.get(connection.get("endId"))
        if not from_node or not to_node:
            continue

        begin = instances_by_id.get(connection.get("beginId"))
        end = instances_by_id.get(connection.get("endId"))
        if not begin or not end:
            continue

        to_side, from_side = detect_direction(begin, end)

        result["edges"].append(
            {
                "id": uuid.uuid4().hex[:16],
                "fromNode": from_node,
                "toNode": to_node,
                "fromSide": from_side,
                "toSide": to_side,
            }
        )

    return result


def create_cards_zip(card_by_id, filename_by_id, title_by_id, asset_index):
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for card_id, card in card_by_id.items():
            filename = filename_by_id[card_id]
            markdown = build_card_markdown(card, card_id, filename_by_id, title_by_id, asset_index)
            zip_file.writestr(filename, markdown)

        written_assets = set()
        for asset in asset_index.values():
            data = asset.get("bytes")
            zip_path = asset.get("zip_path")
            if data and zip_path and zip_path not in written_assets:
                zip_file.writestr(zip_path, data)
                written_assets.add(zip_path)

    zip_buffer.seek(0)
    return zip_buffer


def create_canvas_zip(all_data_json, filename_by_id, cards_path):
    whiteboards = copy.deepcopy(all_data_json.get("whiteBoardList", []))
    used_canvas_names = set()

    whiteboard_by_id = {whiteboard["id"]: whiteboard for whiteboard in whiteboards if whiteboard.get("id")}

    for whiteboard in whiteboards:
        whiteboard["nodes"] = []
        whiteboard["edges"] = []
        whiteboard["sections"] = []

    for card_instance in all_data_json.get("cardInstances", []):
        whiteboard = whiteboard_by_id.get(card_instance.get("whiteboardId"))
        if whiteboard is not None:
            whiteboard["nodes"].append(card_instance)

    for connection in all_data_json.get("connections", []):
        whiteboard = whiteboard_by_id.get(connection.get("whiteboardId"))
        if whiteboard is not None:
            whiteboard["edges"].append(connection)

    for section in all_data_json.get("sections", []):
        whiteboard = whiteboard_by_id.get(section.get("whiteboardId"))
        if whiteboard is not None:
            whiteboard["sections"].append(section)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for whiteboard in whiteboards:
            canvas_json = create_canvas(whiteboard, filename_by_id, cards_path)
            safe_name = sanitize_filename(
                whiteboard.get("name", "Whiteboard"),
                fallback="Whiteboard",
                max_len=80,
            )
            canvas_filename = build_unique_filename(safe_name, used_canvas_names, extension=".canvas")
            zip_file.writestr(canvas_filename, json.dumps(canvas_json, ensure_ascii=False))

    zip_buffer.seek(0)
    return zip_buffer


all_data = st.file_uploader("Upload your Heptabase All-Data.json file")

if all_data is not None:
    try:
        payload = all_data.read()
        all_data_json = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        st.error(f"Invalid JSON file: {exc}")
        st.stop()

    card_list = all_data_json.get("cardList", [])
    card_by_id, filename_by_id, title_by_id = build_card_index(card_list)
    backup_root = normalize_local_path(
        st.text_input(
            "Backup Folder Path (Optional, for local images)",
            "",
            help="If image nodes only contain fileId, set the Heptabase backup folder path to package local *-assets files.",
        )
    )
    if backup_root and not os.path.isdir(backup_root):
        st.warning("Backup Folder Path does not exist or is not accessible. Images may remain external/missing.")
    local_asset_paths = build_local_asset_path_index(backup_root) if backup_root else {}
    image_asset_index = build_image_asset_index(all_data_json, local_asset_paths=local_asset_paths)
    packaged_assets = sum(1 for asset in image_asset_index.values() if asset.get("bytes"))
    st.caption(f"Detected image assets: {len(image_asset_index)} | Packaged local assets: {packaged_assets}")

    cards_zip = create_cards_zip(card_by_id, filename_by_id, title_by_id, image_asset_index)
    st.download_button(
        label="Download Cards",
        data=cards_zip,
        file_name="Cards.zip",
        mime="application/octet-stream",
    )

    cards_path = normalize_cards_path(st.text_input("Your Cards Path", "Cards/"))
    canvas_zip = create_canvas_zip(all_data_json, filename_by_id, cards_path)
    st.download_button(
        label="Download Canvas",
        data=canvas_zip,
        file_name="Canvas.zip",
        mime="application/octet-stream",
    )
