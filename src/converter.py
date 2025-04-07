import hashlib
import json
import logging
import os
import re
import shutil
import time
import traceback
from pathlib import Path

# --- Top Level Imports ---
try:
    import mermaid as md
    from mermaid.graph import Graph

    MERMAID_AVAILABLE = True
except ImportError:
    logging.critical(
        "Mermaid library (python-mermaid) not found. Conversion will fail."
    )
    MERMAID_AVAILABLE = False
# --- End Top Level Imports ---


# --- Logger Setup ---
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(name)s] %(message)s",  # Added logger name
        handlers=[
            logging.FileHandler("mermaid_converter.log", mode="a"),
            logging.StreamHandler(),
        ],
    )
logger = logging.getLogger(__name__)  # Get logger for this module


def load_diagram_config(config_path=None):
    """
    Load diagram configuration from a JSON file.
    Looks for 'diagram_config.json' in the script's directory if no path is given.
    (Implementation remains the same as previous version)
    """
    # Get logger for this function
    func_logger = logging.getLogger(__name__)

    default_config = {
        "default": {"max_width": "600px", "max_height": None, "min_width": None},
        "flowchart": {"max_width": "650px", "max_height": None, "min_width": "300px"},
        "sequence": {"max_width": "550px", "max_height": None, "min_width": "250px"},
        "classdiagram": {
            "max_width": "600px",
            "max_height": None,
            "min_width": "300px",
        },
        "statediagram": {
            "max_width": "550px",
            "max_height": None,
            "min_width": "250px",
        },
        "erdiagram": {"max_width": "700px", "max_height": None, "min_width": "400px"},
        "gantt": {"max_width": "800px", "max_height": None, "min_width": "500px"},
        "pie": {"max_width": "450px", "max_height": "450px", "min_width": "300px"},
    }

    if config_path:
        path_to_check = config_path
        config_source_description = f"specified path '{config_path}'"
    else:
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
            func_logger.warning(
                "__file__ not defined, using CWD for default config lookup."
            )
        path_to_check = os.path.join(script_dir, "diagram_config.json")
        config_source_description = f"default location '{path_to_check}'"
        func_logger.debug(
            f"No config path provided, checking {config_source_description}"
        )

    abs_path_to_check = os.path.abspath(path_to_check)

    if not os.path.isfile(abs_path_to_check):
        if config_path:
            func_logger.warning(
                f"Specified configuration file not found at {abs_path_to_check}. Using default configuration."
            )
        else:
            func_logger.info(
                f"Default configuration file not found at {abs_path_to_check}. Using default configuration."
            )
        return default_config

    func_logger.info(f"Attempting to load configuration from {abs_path_to_check}")
    try:
        with open(abs_path_to_check, "r", encoding="utf-8") as f:
            loaded_config = json.load(f)
        func_logger.info(f"Successfully loaded configuration from {abs_path_to_check}")

        final_config = default_config.copy()
        for key, value in loaded_config.items():
            if (
                key in final_config
                and isinstance(final_config[key], dict)
                and isinstance(value, dict)
            ):
                func_logger.debug(f"Merging config for diagram type: {key}")
                final_config[key].update(value)
            else:
                final_config[key] = value

        if "default" not in final_config:
            final_config["default"] = default_config["default"]

        return final_config

    except json.JSONDecodeError as json_err:
        func_logger.error(
            f"Error decoding JSON from configuration file {abs_path_to_check}: {json_err}. Using default configuration."
        )
        return default_config
    except Exception as e:
        func_logger.error(
            f"An unexpected error occurred loading diagram config from {abs_path_to_check}: {e}. Using default configuration."
        )
        func_logger.error(traceback.format_exc())
        return default_config


def extract_mermaid_blocks(markdown_content):
    """
    Extracts Mermaid code blocks (```mermaid ... ```) from markdown content.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    pattern = r"```mermaid\s+(.*?)```"
    matches = []
    for match in re.finditer(pattern, markdown_content, re.DOTALL):
        block_text = match.group(1).strip()
        start_pos = match.start()
        end_pos = match.end()
        matches.append((block_text, start_pos, end_pos))
        func_logger.debug(f"Found Mermaid block from position {start_pos} to {end_pos}")
    return matches


def create_image_directory(markdown_path, image_dir=None):
    """
    Creates a directory to store the generated images.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    if image_dir:
        abs_image_dir = os.path.abspath(image_dir)
        os.makedirs(abs_image_dir, exist_ok=True)
        func_logger.info(f"Ensured specified image directory exists: {abs_image_dir}")
        return abs_image_dir
    else:
        markdown_abs_path = os.path.abspath(markdown_path)
        markdown_dir = os.path.dirname(markdown_abs_path)
        default_image_dir = os.path.join(markdown_dir, "images")
        os.makedirs(default_image_dir, exist_ok=True)
        func_logger.info(f"Ensured default image directory exists: {default_image_dir}")
        return default_image_dir


def _determine_diagram_type(mermaid_code):
    """
    Helper function to heuristically determine the diagram type from the
    first non-empty line of the Mermaid code block.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    if not mermaid_code:
        return "flowchart"

    first_line = ""
    for line in mermaid_code.strip().splitlines():
        stripped_line = line.strip()
        if stripped_line:
            first_line = stripped_line
            break

    type_map = {
        "sequenceDiagram": "sequence",
        "classDiagram": "classdiagram",
        "stateDiagram": "statediagram",
        "stateDiagram-v2": "statediagram",
        "erDiagram": "erdiagram",
        "gantt": "gantt",
        "pie": "pie",
        "graph": "flowchart",
        "flowchart": "flowchart",
        "journey": "journey",
        "requirementDiagram": "requirement",
    }

    for keyword, diagram_type in type_map.items():
        if first_line.startswith(keyword):
            func_logger.debug(
                f"Detected diagram type '{diagram_type}' from line: {first_line}"
            )
            return diagram_type

    func_logger.warning(
        f"Could not determine diagram type from first line: '{first_line}'. Defaulting to 'flowchart'."
    )
    return "flowchart"


def generate_image_from_mermaid(mermaid_code, output_path, image_format="svg"):
    """
    Generates an image (SVG or PNG) from a Mermaid code string using mermaid-py.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    if not MERMAID_AVAILABLE:
        func_logger.error("Mermaid library not available. Cannot generate image.")
        return False

    func_logger.info(
        f"Generating {image_format.upper()} image for: {os.path.basename(output_path)}"
    )
    try:
        graph_start_time = time.time()
        diagram_type = _determine_diagram_type(mermaid_code)
        graph_obj = Graph(diagram_type, mermaid_code)
        graph_end_time = time.time()
        func_logger.debug(
            f"Graph object creation took {graph_end_time - graph_start_time:.4f} seconds."
        )

        mermaid_instance = None
        instantiation_start_time = time.time()
        try:
            mermaid_instance = md.Mermaid(graph_obj)
        except Exception as init_err:
            func_logger.error(
                f"Error instantiating Mermaid object: {init_err}", exc_info=True
            )
            return False
        finally:
            instantiation_end_time = time.time()
            instantiation_duration = instantiation_end_time - instantiation_start_time
            func_logger.info(
                f"Mermaid object instantiation took {instantiation_duration:.2f} seconds."
            )

        output_format = image_format.lower()
        render_start_time = time.time()
        generation_success = False

        try:
            if output_format == "svg":
                mermaid_instance.to_svg(output_path)
                generation_success = True
            elif output_format == "png":
                mermaid_instance.to_png(output_path)
                generation_success = True
            else:
                func_logger.error(f"Unsupported image format requested: {image_format}")
                if mermaid_instance:
                    del mermaid_instance
                return False
        except Exception as render_err:
            func_logger.error(
                f"Error during {output_format.upper()} generation: {render_err}",
                exc_info=True,
            )
            generation_success = False
        finally:
            render_end_time = time.time()
            render_duration = render_end_time - render_start_time
            func_logger.info(
                f"Mermaid {output_format.upper()} generation call took {render_duration:.2f} seconds."
            )
            if mermaid_instance:
                del mermaid_instance

        if (
            generation_success
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 0
        ):
            func_logger.debug(f"Successfully generated image file: {output_path}")
            return True
        else:
            # Log failure reasons more concisely
            reason = (
                "rendering error"
                if not generation_success
                else (
                    "output file missing"
                    if not os.path.exists(output_path)
                    else (
                        "output file empty"
                        if os.path.exists(output_path)
                        and os.path.getsize(output_path) == 0
                        else "unknown reason"
                    )
                )
            )
            func_logger.error(
                f"Image generation failed for {os.path.basename(output_path)}: {reason}"
            )

            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    func_logger.warning(
                        f"Removed empty/failed output file: {os.path.basename(output_path)}"
                    )
                except OSError as rm_err:
                    func_logger.error(
                        f"Failed to remove empty/failed output file {output_path}: {rm_err}"
                    )
            return False

    except Exception as e:
        func_logger.error(
            f"Unexpected error in generate_image_from_mermaid for {output_path}: {e}",
            exc_info=True,
        )
        return False


def create_image_name(prefix, index, mermaid_code, image_format="svg"):
    """
    Creates a unique and relatively safe filename for the generated image.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    code_hash = hashlib.md5(mermaid_code.encode("utf-8")).hexdigest()[:8]
    safe_prefix = re.sub(r"[^\w\-]+", "", prefix)
    filename = f"{safe_prefix}-{index}-{code_hash}.{image_format.lower()}"
    func_logger.debug(f"Generated image filename: {filename}")
    return filename


def replace_mermaid_with_images_enhanced(
    markdown_content,
    mermaid_blocks,
    image_paths_info,
    diagram_config,
    use_html_wrapper=True,
):
    """
    Replaces Mermaid code blocks in the markdown content with image references or error comments.
    (Implementation remains the same as previous version)
    """
    func_logger = logging.getLogger(__name__)
    new_content = markdown_content
    offset = 0
    successful_replacements = 0

    for i, (block_text, start_pos, end_pos) in enumerate(mermaid_blocks):
        relative_image_path, success_flag = image_paths_info[i]
        adj_start = start_pos + offset
        adj_end = end_pos + offset
        replacement_text = ""

        if success_flag and relative_image_path:
            is_svg = relative_image_path.lower().endswith(".svg")
            diagram_type = _determine_diagram_type(block_text)

            if not isinstance(diagram_config, dict):
                func_logger.warning(
                    f"Invalid diagram_config type ({type(diagram_config)}). Using empty config."
                )
                diagram_config = {}
            config = diagram_config.get(diagram_type, diagram_config.get("default", {}))
            if not isinstance(config, dict):
                func_logger.warning(
                    f"Invalid config type for diagram '{diagram_type}'. Using empty settings."
                )
                config = {}

            max_width = config.get("max_width", "600px")
            alt_text = f"Mermaid Diagram: {diagram_type}"

            if use_html_wrapper and is_svg:
                replacement_text = f"""

<div style="max-width: {max_width}; margin: 1em auto; text-align: center;">
    <img src="{relative_image_path}" alt="{alt_text}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />
</div>

"""
            else:
                replacement_text = f"\n\n![{alt_text}]({relative_image_path})\n\n"

            successful_replacements += 1
            # func_logger.debug(f"Replacing block {i+1} with image link: {relative_image_path}")

        else:
            warning_comment = "\n\n\n"
            original_block_formatted = f"```mermaid\n{block_text.strip()}\n```\n"
            replacement_text = warning_comment + original_block_formatted
            func_logger.warning(
                f"Keeping original code block {i+1} due to generation failure."
            )

        new_content = new_content[:adj_start] + replacement_text + new_content[adj_end:]
        offset += len(replacement_text) - (end_pos - start_pos)

    return new_content, successful_replacements


def process_markdown_file(
    file_path,
    image_prefix="diagram",
    image_format="svg",
    image_dir=None,
    diagram_config=None,
    use_html_wrapper=True,
    output_suffix="-img",
    # Note: move_original and add_readme args are removed, caller handles this
):
    """
    Processes a Markdown file: finds Mermaid blocks, converts them to images,
    generates the potentially modified markdown content string.

    Args:
        file_path (str): Path to the input Markdown file.
        image_prefix (str, optional): Prefix for generated image filenames. Defaults to "diagram".
        image_format (str, optional): Output image format ('svg' or 'png'). Defaults to "svg".
        image_dir (str, optional): Specific directory for images. Defaults to None (creates 'images/' subdir).
        diagram_config (dict, optional): Diagram configuration dictionary. Defaults to None (loads default config).
        use_html_wrapper (bool, optional): Wrap SVG images in HTML `<div><img>` tag. Defaults to True.
        output_suffix (str, optional): Suffix added to the original filename for the output file path calculation. Defaults to "-img".

    Returns:
        dict: A dictionary containing results and information for the caller:
              - input_file_path (str): Absolute path to the input file.
              - total_diagrams (int)
              - successful_conversions (int)
              - failed_conversions (int)
              - output_file_path (str): Calculated absolute path for the potential output markdown file.
              - image_directory (str): Absolute path to the directory containing images.
              - all_conversions_successful (bool): True if all diagrams converted without error.
              - generated_image_paths (list[str]): List of absolute paths to successfully generated images.
              - new_content (str): The generated markdown content string (with image links or error comments).
              - error (str | None): An error message if a critical error occurred early.
    """
    # Get logger for this function
    func_logger = logging.getLogger(__name__)

    # Ensure diagram configuration is loaded if not provided
    if diagram_config is None:
        func_logger.debug("Diagram config not provided, loading default.")
        diagram_config = load_diagram_config()

    # Initialize statistics and results dictionary
    stats = {
        "input_file_path": os.path.abspath(file_path),
        "total_diagrams": 0,
        "successful_conversions": 0,
        "failed_conversions": 0,
        "output_file_path": "",  # Calculated path for potential output file
        "image_directory": "",
        "all_conversions_successful": False,  # Assume failure until proven otherwise
        "generated_image_paths": [],  # List of successfully created images
        "new_content": "",  # The generated markdown content
        "error": None,  # For early critical errors
    }

    try:
        # --- Input File Validation ---
        abs_file_path = stats["input_file_path"]
        func_logger.info(f"Starting processing for file: {abs_file_path}")
        if not os.path.isfile(abs_file_path):
            msg = f"Input path is not a file or does not exist: {abs_file_path}"
            func_logger.error(msg)
            stats["error"] = msg
            return stats  # Return stats with error

        # --- Read Input File Content ---
        try:
            with open(abs_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            func_logger.debug(f"Successfully read input file: {abs_file_path}")
        except Exception as read_err:
            msg = f"Failed to read input file {abs_file_path}: {read_err}"
            func_logger.error(msg, exc_info=True)
            stats["error"] = msg
            return stats  # Return stats with error

        # --- Extract Mermaid Blocks ---
        mermaid_blocks = extract_mermaid_blocks(content)
        stats["total_diagrams"] = len(mermaid_blocks)
        if not mermaid_blocks:
            func_logger.info(f"No Mermaid diagrams found in {abs_file_path}.")
            # If no diagrams, it's technically successful, return original content
            stats["all_conversions_successful"] = True
            stats["new_content"] = content  # Return original content
            return stats  # Return stats indicating no diagrams found

        func_logger.info(
            f"Found {len(mermaid_blocks)} Mermaid diagram(s) in {abs_file_path}"
        )

        # --- Prepare Image Directory ---
        abs_image_dir = create_image_directory(abs_file_path, image_dir)
        stats["image_directory"] = abs_image_dir
        func_logger.info(f"Using image directory: {abs_image_dir}")

        # --- Process Each Diagram ---
        image_paths_info = []  # Stores (relative_image_path, success_flag) tuples
        generated_images_list = []  # Stores absolute paths of successful images
        output_md_dir = os.path.dirname(
            abs_file_path
        )  # Directory where original file resides
        all_successful_flag = True  # Track overall success

        for i, (block, _, _) in enumerate(mermaid_blocks):
            diagram_index = i + 1
            func_logger.info(
                f"--- Processing Diagram {diagram_index}/{len(mermaid_blocks)} ---"
            )
            image_name = create_image_name(
                image_prefix, diagram_index, block, image_format
            )
            abs_image_path = os.path.join(abs_image_dir, image_name)

            success = generate_image_from_mermaid(block, abs_image_path, image_format)

            if success:
                generated_images_list.append(
                    abs_image_path
                )  # Add to list for potential rollback
                try:
                    rel_path = os.path.relpath(
                        abs_image_path, start=output_md_dir
                    ).replace("\\", "/")
                except ValueError:
                    func_logger.warning(
                        f"Cannot create relative path for image {abs_image_path}. Using absolute URI."
                    )
                    rel_path = Path(abs_image_path).as_uri()
                image_paths_info.append((rel_path, True))
                stats["successful_conversions"] += 1
            else:
                image_paths_info.append((None, False))
                stats["failed_conversions"] += 1
                all_successful_flag = False  # Mark failure if any diagram fails

        stats["all_conversions_successful"] = all_successful_flag
        stats["generated_image_paths"] = generated_images_list

        # --- Determine Potential Output Filename ---
        abs_file_path_obj = Path(abs_file_path)
        output_file_name = f"{abs_file_path_obj.stem}{output_suffix}.md"
        abs_output_file = abs_file_path_obj.parent / output_file_name
        stats["output_file_path"] = str(abs_output_file)  # Store potential output path

        # --- Generate the New Markdown Content String ---
        # This happens regardless of success/failure; failed blocks are commented out
        func_logger.info("Generating final markdown content string...")
        new_content_str, successful_replacements = replace_mermaid_with_images_enhanced(
            content, mermaid_blocks, image_paths_info, diagram_config, use_html_wrapper
        )
        stats["new_content"] = new_content_str  # Store the generated content

        if successful_replacements != stats["successful_conversions"]:
            func_logger.warning(
                f"Mismatch Alert: {stats['successful_conversions']} successful conversions vs {successful_replacements} replacements."
            )

        # --- Return Results ---
        func_logger.info(
            f"Finished processing diagrams for {file_path}. Success: {all_successful_flag}"
        )
        return stats  # Return the populated statistics dictionary

    except Exception as e:
        # Catch any unexpected top-level errors during processing
        msg = f"An unexpected error occurred processing file {file_path}: {str(e)}"
        func_logger.error(msg, exc_info=True)
        stats["error"] = msg
        stats["all_conversions_successful"] = False  # Ensure failure state
        return stats  # Return whatever stats were collected before the error


# --- Example Usage (if script is run directly) ---
if __name__ == "__main__":
    print("Converter module loaded. Contains functions for processing markdown files.")
    # Add test code here if needed, similar to previous versions,
    # but note that this function no longer writes files directly.
    # You would call process_markdown_file and then inspect the returned 'stats' dict.
