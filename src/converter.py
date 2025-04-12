import hashlib
import json
import logging
import os
import re
import shutil
import time
import traceback
from pathlib import Path

import requests  # Added for Kroki

# --- Top Level Imports for Mermaid Library ---
try:
    # Attempt to import the mermaid library components
    import mermaid as md
    from mermaid.graph import Graph

    MERMAID_AVAILABLE = True
    # Log success only if needed for debugging, otherwise it's assumed available if no error
    # logging.getLogger(__name__).debug("Successfully imported python-mermaid library.")
except ImportError:
    # Log critical error immediately if library is expected but missing
    # This log will appear if the library isn't installed when converter.py is loaded.
    logging.getLogger(__name__).critical(
        "Mermaid library (python-mermaid) not found. 'library' method will fail if selected."
    )
    MERMAID_AVAILABLE = False

    # Define dummy classes/functions to prevent NameErrors if MERMAID_AVAILABLE is checked later
    # These won't actually be functional but allow the script to load.
    class Graph:
        pass

    class md:
        Mermaid = None


# --- End Top Level Imports ---


# --- Logger Setup ---
# Basic setup if no handlers exist (e.g., when this module is run standalone
# or before the main application configures logging).
# Applications using this module (like main.py or gui.py) should configure
# the root logger themselves for more robust logging (e.g., to files).
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,  # Default level for standalone use
        format="%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
        handlers=[logging.StreamHandler()],  # Simple console logging
    )
# Get a logger specific to this module
logger = logging.getLogger(__name__)

# --- Constants ---
DEFAULT_KROKI_URL = "http://localhost:8000"  # Default Kroki instance URL


# --- Configuration Loading ---
def load_diagram_config(config_path=None):
    """
    Loads diagram configuration (e.g., styling hints like max_width) from a JSON file.
    Looks for 'diagram_config.json' in the script's directory if no path is given.
    Merges loaded config with internal defaults.

    Args:
        config_path (str, optional): Path to the JSON configuration file. Defaults to None.

    Returns:
        dict: The final configuration dictionary, merged with defaults.
    """
    func_logger = logging.getLogger(__name__)
    # Define internal default settings for various diagram types
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
        # Add other diagram types and their default settings here
    }

    # Determine the path to check for the config file
    if config_path:
        path_to_check = config_path
        config_source_description = f"specified path '{config_path}'"
    else:
        # Default to looking for 'diagram_config.json' next to this script
        try:
            # Get the directory where this converter.py script resides
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            # Fallback if __file__ is not defined (e.g., in some interactive environments)
            script_dir = os.getcwd()
            func_logger.warning(
                "__file__ not defined, using Current Working Directory for default config lookup."
            )
        path_to_check = os.path.join(script_dir, "diagram_config.json")
        config_source_description = f"default location '{path_to_check}'"
        func_logger.debug(
            f"No config path provided, checking {config_source_description}"
        )

    # Get the absolute path for consistency
    abs_path_to_check = os.path.abspath(path_to_check)

    # Check if the file exists
    if not os.path.isfile(abs_path_to_check):
        # Log appropriately depending on whether a specific path was given
        if config_path:
            func_logger.warning(
                f"Specified configuration file not found at {abs_path_to_check}. Using default configuration."
            )
        else:
            func_logger.info(
                f"Default configuration file not found at {abs_path_to_check}. Using default configuration."
            )
        # Return the hardcoded defaults if file is missing
        return default_config

    # Attempt to load the configuration from the file
    func_logger.info(f"Attempting to load configuration from {abs_path_to_check}")
    try:
        with open(abs_path_to_check, "r", encoding="utf-8") as f:
            loaded_config = json.load(f)
        func_logger.info(f"Successfully loaded configuration from {abs_path_to_check}")

        # Merge the loaded configuration with the defaults.
        # The loaded config overrides or adds to the defaults.
        final_config = default_config.copy()  # Start with defaults
        for key, value in loaded_config.items():
            # If the key exists in defaults and both are dictionaries, merge them
            if (
                key in final_config
                and isinstance(final_config[key], dict)
                and isinstance(value, dict)
            ):
                func_logger.debug(f"Merging config for diagram type: {key}")
                final_config[key].update(
                    value
                )  # Update the default dict with loaded values
            else:
                # Otherwise, overwrite or add the key/value from the loaded config
                final_config[key] = value

        # Ensure the 'default' key always exists, even if the loaded file overwrote it incorrectly
        if "default" not in final_config or not isinstance(
            final_config["default"], dict
        ):
            func_logger.warning(
                "Loaded config missing or has invalid 'default' key. Restoring default settings for 'default'."
            )
            final_config["default"] = default_config["default"]

        return final_config

    except json.JSONDecodeError as json_err:
        # Handle errors if the file is not valid JSON
        func_logger.error(
            f"Error decoding JSON from configuration file {abs_path_to_check}: {json_err}. Using default configuration."
        )
        return default_config  # Fallback to defaults
    except Exception as e:
        # Handle other potential errors during file reading or processing
        func_logger.error(
            f"An unexpected error occurred loading diagram config from {abs_path_to_check}: {e}. Using default configuration."
        )
        func_logger.error(
            traceback.format_exc()
        )  # Log the full traceback for debugging
        return default_config  # Fallback to defaults


# --- Core Logic ---
def extract_mermaid_blocks(markdown_content):
    """
    Extracts Mermaid code blocks (```mermaid ... ```) from markdown content.

    Args:
        markdown_content (str): The input markdown text.

    Returns:
        list: A list of tuples, where each tuple contains:
              (mermaid_code_string, start_position, end_position)
              for each found block. Returns an empty list if no blocks are found.
    """
    func_logger = logging.getLogger(__name__)
    # Regex to find markdown code blocks fenced with ```mermaid
    # - ```mermaid: Matches the opening fence
    # - \s+: Matches one or more whitespace characters (allows flexibility)
    # - (.*?): Captures the content inside the block (non-greedy)
    # - ```: Matches the closing fence
    # - re.DOTALL: Allows '.' to match newline characters, so the content can span multiple lines
    pattern = r"```mermaid\s+(.*?)```"
    matches = []
    # Find all non-overlapping matches in the content
    for match in re.finditer(pattern, markdown_content, re.DOTALL):
        # Extract the captured group (the mermaid code itself) and strip leading/trailing whitespace
        block_text = match.group(1).strip()
        # Get the start and end character positions of the entire match (including fences)
        start_pos = match.start()
        end_pos = match.end()
        # Store the extracted code and its position
        matches.append((block_text, start_pos, end_pos))
        func_logger.debug(f"Found Mermaid block from position {start_pos} to {end_pos}")
    # Return the list of found blocks (or an empty list if none were found)
    return matches


def create_image_directory(markdown_path, image_dir=None):
    """
    Creates a directory to store the generated images.
    If `image_dir` is provided, it uses that path.
    Otherwise, it creates an 'images' subdirectory in the same directory as the markdown file.

    Args:
        markdown_path (str): The path to the input markdown file.
        image_dir (str, optional): A specific directory path to use for images. Defaults to None.

    Returns:
        str: The absolute path to the created or verified image directory.

    Raises:
        OSError: If the directory cannot be created due to permissions or other OS issues.
    """
    func_logger = logging.getLogger(__name__)
    # Determine the target directory path
    if image_dir:
        # If a specific directory is provided, use its absolute path
        abs_image_dir = os.path.abspath(image_dir)
        dir_source = "specified"
    else:
        # Default behavior: create 'images' subdirectory next to the markdown file
        markdown_abs_path = os.path.abspath(markdown_path)
        markdown_dir = os.path.dirname(markdown_abs_path)
        abs_image_dir = os.path.join(markdown_dir, "images")
        dir_source = "default ('images/' subdir)"

    func_logger.info(f"Ensuring {dir_source} image directory exists: {abs_image_dir}")
    # Create the directory.
    # `exist_ok=True` prevents an error if the directory already exists.
    try:
        os.makedirs(abs_image_dir, exist_ok=True)
        # Return the absolute path to the directory
        return abs_image_dir
    except OSError as e:
        # Log and re-raise error if directory creation fails
        func_logger.error(
            f"Failed to create image directory {abs_image_dir}: {e}", exc_info=True
        )
        raise  # Propagate the error to the caller


def _determine_diagram_type(mermaid_code):
    """
    Helper function to heuristically determine the diagram type (e.g., 'flowchart',
    'sequence', 'classDiagram') from the first non-empty, non-comment line
    of the Mermaid code block. Used for applying type-specific configurations.

    Args:
        mermaid_code (str): The Mermaid diagram definition string.

    Returns:
        str: The determined diagram type (lowercase string), defaulting to 'flowchart'.
    """
    func_logger = logging.getLogger(__name__)
    # Handle empty input
    if not mermaid_code:
        return "flowchart"  # Default if no code provided

    first_significant_line = ""
    # Iterate through lines to find the first non-empty, non-comment line
    for line in mermaid_code.strip().splitlines():
        stripped_line = line.strip()
        # Ignore empty lines and lines starting with '%%' (Mermaid comments)
        if stripped_line and not stripped_line.startswith("%%"):
            first_significant_line = stripped_line
            break  # Found the first relevant line

    # Map known Mermaid keywords (usually at the start of the definition) to type names
    # Use lowercase type names for consistency in config lookups.
    type_map = {
        "sequenceDiagram": "sequence",
        "classDiagram": "classdiagram",
        "stateDiagram-v2": "statediagram",  # v2 is common
        "stateDiagram": "statediagram",
        "erDiagram": "erdiagram",
        "journey": "journey",
        "gantt": "gantt",
        "pie": "pie",
        "flowchart": "flowchart",  # Explicit flowchart keyword
        "graph": "flowchart",  # 'graph' is often used for flowcharts (TD, LR, etc.)
        "requirementDiagram": "requirement",
        "gitGraph": "gitgraph",
        # Add other diagram type keywords as needed
    }

    # Check if the first significant line starts with any known keyword
    for keyword, diagram_type in type_map.items():
        # Case-insensitive check might be more robust, but Mermaid keywords are usually specific.
        # Using startswith is generally sufficient.
        if first_significant_line.startswith(keyword):
            func_logger.debug(
                f"Detected diagram type '{diagram_type}' from line: {first_significant_line}"
            )
            return diagram_type  # Return the mapped type name

    # If no keyword is matched, default to 'flowchart' as it's a common base type
    func_logger.warning(
        f"Could not determine diagram type from first significant line: '{first_significant_line}'. Defaulting to 'flowchart'."
    )
    return "flowchart"


# --- Generation Functions ---


def generate_image_from_mermaid_library(mermaid_code, output_path, image_format="svg"):
    """
    Generates an image (SVG or PNG) from a Mermaid code string using the `python-mermaid` library.

    Args:
        mermaid_code (str): The Mermaid diagram definition.
        output_path (str): The full path where the generated image should be saved.
        image_format (str): The desired image format ('svg' or 'png'). Defaults to 'svg'.

    Returns:
        bool: True if image generation was successful, False otherwise.
    """
    func_logger = logging.getLogger(__name__)
    # Check if the library was successfully imported earlier
    if not MERMAID_AVAILABLE:
        func_logger.error(
            "Mermaid library (python-mermaid) not available. Cannot generate image using 'library' method."
        )
        return False  # Cannot proceed without the library

    # Log the start of the generation process
    func_logger.info(
        f"[Library] Generating {image_format.upper()} image for: {os.path.basename(output_path)}"
    )

    try:
        # --- 1. Create Graph Object ---
        graph_creation_start_time = time.time()
        # Determine the diagram type to pass to the Graph constructor
        diagram_type = _determine_diagram_type(mermaid_code)
        # Create the Graph object using the imported `Graph` class
        graph_obj = Graph(diagram_type, mermaid_code)
        graph_creation_end_time = time.time()
        func_logger.debug(
            f"[Library] Graph object creation took {graph_creation_end_time - graph_creation_start_time:.4f} seconds."
        )

        # --- 2. Instantiate Mermaid Object ---
        mermaid_instance = None  # Initialize to None
        instantiation_start_time = time.time()
        try:
            # Instantiate the Mermaid object using the imported `md` module
            mermaid_instance = md.Mermaid(graph_obj)
        except Exception as init_err:
            # Log error if Mermaid object creation fails (e.g., browser issues)
            func_logger.error(
                f"[Library] Error instantiating Mermaid object: {init_err}",
                exc_info=True,
            )
            return False  # Cannot proceed if instantiation fails
        finally:
            # Log instantiation time, especially if it's significant
            instantiation_end_time = time.time()
            instantiation_duration = instantiation_end_time - instantiation_start_time
            if instantiation_duration > 0.1:  # Log only if it takes noticeable time
                func_logger.info(
                    f"[Library] Mermaid object instantiation took {instantiation_duration:.2f} seconds."
                )

        # --- 3. Generate Output File ---
        output_format = image_format.lower()  # Ensure lowercase format string
        render_start_time = time.time()
        generation_success = False  # Flag to track success
        try:
            # Call the appropriate method based on the desired format
            if output_format == "svg":
                mermaid_instance.to_svg(output_path)
                generation_success = True
            elif output_format == "png":
                mermaid_instance.to_png(output_path)
                generation_success = True
            else:
                # Handle unsupported formats
                func_logger.error(
                    f"[Library] Unsupported image format requested: {image_format}"
                )
                # generation_success remains False
        except Exception as render_err:
            # Log errors during the actual rendering process
            func_logger.error(
                f"[Library] Error during {output_format.upper()} generation: {render_err}",
                exc_info=True,
            )
            generation_success = False  # Ensure flag is false on error
        finally:
            # Log rendering time
            render_end_time = time.time()
            render_duration = render_end_time - render_start_time
            if render_duration > 0.1:  # Log only if noticeable
                func_logger.info(
                    f"[Library] Mermaid {output_format.upper()} generation call took {render_duration:.2f} seconds."
                )
            # --- 4. Cleanup (Optional but Recommended) ---
            # Explicitly delete the instance if mermaid-py holds resources (like browser contexts)
            # Check the library's documentation for recommended cleanup procedures.
            if mermaid_instance:
                # Example: If mermaid_instance had a browser context, you might call mermaid_instance.cleanup() or similar
                del mermaid_instance  # Basic Python object cleanup

        # --- 5. Verify Output ---
        # Check if generation was marked successful AND the file exists AND is not empty
        if (
            generation_success
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 0
        ):
            func_logger.debug(
                f"[Library] Successfully generated image file: {output_path}"
            )
            return True  # Success!
        else:
            # Determine the reason for failure for better logging
            if not generation_success:
                reason = "rendering error"
            elif not os.path.exists(output_path):
                reason = "output file missing"
            else:
                reason = "output file empty"
            func_logger.error(
                f"[Library] Image generation failed for {os.path.basename(output_path)}: {reason}"
            )
            # Attempt to remove the failed/empty output file to avoid confusion
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    func_logger.warning(
                        f"[Library] Removed empty/failed output file: {os.path.basename(output_path)}"
                    )
                except OSError as rm_err:
                    # Log error if removal fails (e.g., permissions)
                    func_logger.error(
                        f"[Library] Failed to remove empty/failed output file {output_path}: {rm_err}"
                    )
            return False  # Failure

    except Exception as e:
        # Catch any unexpected errors during the process
        func_logger.error(
            f"[Library] Unexpected error in generate_image_from_mermaid_library for {output_path}: {e}",
            exc_info=True,
        )
        return False  # Failure


def generate_image_with_kroki(
    mermaid_code, output_path, image_format="svg", kroki_url=None
):
    """
    Generates an image (SVG or PNG) from a Mermaid code string using a Kroki HTTP API instance.

    Args:
        mermaid_code (str): The Mermaid diagram definition.
        output_path (str): The full path where the generated image should be saved.
        image_format (str): The desired image format ('svg' or 'png'). Defaults to 'svg'.
        kroki_url (str, optional): The base URL of the Kroki service.
                                   Defaults to DEFAULT_KROKI_URL if None or empty.

    Returns:
        bool: True if image generation was successful, False otherwise.
    """
    func_logger = logging.getLogger(__name__)
    # Define the diagram type as understood by Kroki for Mermaid
    diagram_type = "mermaid"
    # Ensure format is lowercase for the URL
    output_format_kroki = image_format.lower()

    # Determine the effective Kroki URL, using the default if necessary
    effective_kroki_url = kroki_url or DEFAULT_KROKI_URL
    # Basic validation of the URL format
    if not effective_kroki_url.startswith(("http://", "https://")):
        func_logger.error(
            f"[Kroki] Invalid Kroki URL provided: '{effective_kroki_url}'. Must start with http:// or https://."
        )
        return False

    # Construct the full POST URL for the Kroki API endpoint
    # Format: {kroki_base_url}/{diagram_type}/{output_format}
    post_url = f"{effective_kroki_url.rstrip('/')}/{diagram_type}/{output_format_kroki}"

    # Log the attempt
    func_logger.info(
        f"[Kroki] Attempting to generate {output_format_kroki.upper()} from Kroki at: {post_url} for: {os.path.basename(output_path)}"
    )
    # Optionally log the diagram definition being sent (can be verbose)
    # func_logger.debug(f"[Kroki] Diagram definition:\n{mermaid_code}")

    try:
        # --- 1. Send POST Request to Kroki ---
        # Send the Mermaid code in the request body, encoded as UTF-8 bytes.
        # Set a reasonable timeout for the request.
        response = requests.post(
            post_url,
            data=mermaid_code.encode("utf-8"),
            headers={"Content-Type": "text/plain"},  # Explicitly set content type
            timeout=30,  # seconds
        )

        # --- 2. Check Response Status ---
        # Raise an `HTTPError` exception for bad status codes (4xx or 5xx).
        # This automatically checks if `response.status_code` indicates an error.
        response.raise_for_status()

        # --- 3. Save Response Content to File ---
        # The image data is in `response.content` (as bytes).
        # Write these bytes directly to the output file in binary mode ('wb').
        with open(output_path, "wb") as f:
            f.write(response.content)

        # --- 4. Verify Output File ---
        # Check if the file was actually created and contains data.
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            func_logger.debug(
                f"[Kroki] Successfully generated image file via Kroki: {output_path}"
            )
            return True  # Success!
        else:
            # Handle cases where Kroki might return a 200 OK but an empty body
            func_logger.error(
                f"[Kroki] Kroki generated an empty file for {os.path.basename(output_path)} despite a successful status code."
            )
            # Attempt to remove the empty file
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass  # Ignore error if removal fails
            return False  # Failure

    # --- Error Handling for `requests` ---
    except requests.exceptions.ConnectionError as e:
        # Handle errors where the client cannot connect to the server
        func_logger.error(
            f"[Kroki] Could not connect to the Kroki server at {effective_kroki_url}. Is it running and accessible? Details: {e}"
        )
        return False
    except requests.exceptions.Timeout:
        # Handle errors where the request takes too long
        func_logger.error(
            f"[Kroki] Request to Kroki server timed out ({post_url}). Server might be slow or unresponsive."
        )
        return False
    except requests.exceptions.HTTPError as e:
        # Handle errors reported by the Kroki server (e.g., 400 Bad Request for invalid syntax, 500 Internal Server Error)
        func_logger.error(
            f"[Kroki] Kroki server returned HTTP {e.response.status_code} for {os.path.basename(output_path)}."
        )
        # Log the response body from Kroki, as it often contains useful error details. Limit size to avoid huge logs.
        error_details = e.response.text[:500]  # Log first 500 characters
        func_logger.error(f"[Kroki] Response body (up to 500 chars): {error_details}")
        return False
    except requests.exceptions.RequestException as e:
        # Catch any other potential errors from the requests library
        func_logger.error(
            f"[Kroki] An unexpected error occurred during the request to Kroki for {output_path}: {e}",
            exc_info=True,
        )
        return False
    # --- Generic Error Handling ---
    except Exception as e:
        # Catch any other unexpected errors during the process (e.g., file writing errors)
        func_logger.error(
            f"[Kroki] Unexpected error during Kroki generation for {output_path}: {e}",
            exc_info=True,
        )
        return False


# --- Dispatcher Function ---
def generate_diagram_image(
    method, mermaid_code, output_path, image_format, kroki_url=None
):
    """
    Dispatches the image generation task to the appropriate function (`library` or `kroki`)
    based on the specified method.

    Args:
        method (str): The chosen conversion method ('library' or 'kroki').
        mermaid_code (str): The Mermaid diagram definition.
        output_path (str): The full path where the generated image should be saved.
        image_format (str): The desired image format ('svg' or 'png').
        kroki_url (str, optional): The base URL of the Kroki service (only used if method='kroki').

    Returns:
        bool: True if image generation was successful using the chosen method, False otherwise.
    """
    func_logger = logging.getLogger(__name__)
    # Log which method is being called
    func_logger.debug(
        f"Dispatching generation: method='{method}', format='{image_format}', output='{os.path.basename(output_path)}'"
    )

    # Call the corresponding generation function based on the method string
    if method == "library":
        return generate_image_from_mermaid_library(
            mermaid_code, output_path, image_format
        )
    elif method == "kroki":
        # Pass the kroki_url; the kroki function handles defaulting if None is passed
        return generate_image_with_kroki(
            mermaid_code, output_path, image_format, kroki_url
        )
    else:
        # Handle cases where an invalid method name is provided
        func_logger.error(
            f"Unknown generation method requested: '{method}'. Supported methods: 'library', 'kroki'."
        )
        return False  # Invalid method specified


# --- Utility Functions ---
def create_image_name(prefix, index, mermaid_code, image_format="svg"):
    """
    Creates a unique and relatively safe filename for the generated image.
    Includes a short hash of the diagram code to differentiate identical diagrams
    if they appear multiple times.

    Args:
        prefix (str): A prefix for the filename (e.g., 'diagram').
        index (int): The index (1-based) of the diagram within the document.
        mermaid_code (str): The Mermaid code string (used to generate a hash).
        image_format (str): The image format ('svg' or 'png'). Defaults to 'svg'.

    Returns:
        str: The generated filename (e.g., 'diagram-1-a3b8ef0d.svg').
    """
    func_logger = logging.getLogger(__name__)
    # Generate a short MD5 hash of the diagram code to make the filename unique
    # even if the prefix and index are the same for different content.
    # Using first 8 characters of hex digest is usually sufficient for uniqueness within a doc.
    code_hash = hashlib.md5(mermaid_code.encode("utf-8")).hexdigest()[:8]

    # Sanitize the user-provided prefix to remove characters potentially unsafe for filenames.
    # Allow letters, numbers, underscore, hyphen, dot. Replace others.
    safe_prefix = re.sub(r"[^\w\-.]+", "", prefix)
    # Ensure the prefix isn't empty after sanitization; default to 'diagram' if it is.
    safe_prefix = safe_prefix or "diagram"

    # Construct the filename
    filename = f"{safe_prefix}-{index}-{code_hash}.{image_format.lower()}"
    func_logger.debug(f"Generated image filename: {filename}")
    return filename


def replace_mermaid_with_images_enhanced(
    markdown_content,
    mermaid_blocks,
    image_paths_info,  # List of tuples: (relative_image_path, success_flag)
    diagram_config,  # Loaded configuration for styling hints
    use_html_wrapper=True,  # Flag to choose between HTML wrapper and plain Markdown
):
    """
    Replaces Mermaid code blocks in the markdown content with image references
    (using either an HTML wrapper or plain Markdown syntax) or leaves the original
    block commented out if conversion failed.

    Args:
        markdown_content (str): The original markdown content.
        mermaid_blocks (list): List of tuples (block_text, start_pos, end_pos) from extract_mermaid_blocks.
        image_paths_info (list): List of tuples (relative_image_path, success_flag) corresponding
                                 to each block in mermaid_blocks. `relative_image_path` is None
                                 if `success_flag` is False.
        diagram_config (dict): Loaded diagram configuration for styling (e.g., max_width).
        use_html_wrapper (bool): If True, use an HTML `<div><img></div>` wrapper.
                                 If False, use plain Markdown `![alt](path)`. Defaults to True.

    Returns:
        tuple: A tuple containing:
               (new_markdown_content_string, count_of_successful_replacements)
    """
    func_logger = logging.getLogger(__name__)
    new_content = markdown_content  # Start with the original content
    offset = 0  # Tracks the change in string length due to replacements
    successful_replacements = 0  # Counter for successful image links

    # Iterate through the found mermaid blocks and their corresponding conversion results
    for i, (block_text, start_pos, end_pos) in enumerate(mermaid_blocks):
        # Get the result for this specific block
        relative_image_path, success_flag = image_paths_info[i]

        # Calculate the adjusted start/end positions in the potentially modified string
        adj_start = start_pos + offset
        adj_end = end_pos + offset

        replacement_text = ""  # Initialize the text that will replace the block

        # --- Case 1: Conversion Succeeded ---
        if success_flag and relative_image_path:
            # Determine the diagram type to fetch specific styling config
            diagram_type = _determine_diagram_type(block_text)

            # Safely get the configuration for this diagram type or fall back to 'default'
            if not isinstance(diagram_config, dict):
                func_logger.warning(
                    f"Invalid diagram_config type ({type(diagram_config)}). Using empty config."
                )
                diagram_config = {}  # Ensure it's a dict
            # Get specific config, fallback to default, fallback to empty dict
            config = diagram_config.get(diagram_type, diagram_config.get("default", {}))
            if not isinstance(config, dict):
                func_logger.warning(
                    f"Invalid config type for diagram '{diagram_type}' or 'default'. Using empty settings."
                )
                config = {}  # Ensure it's a dict

            # Extract styling hints from config (provide defaults)
            max_width = config.get(
                "max_width", "600px"
            )  # Default max-width if not specified
            alt_text = f"Mermaid Diagram: {diagram_type}"  # Generate basic alt text

            # --- Choose Replacement Format ---
            if use_html_wrapper:
                # Using an HTML wrapper allows for better control over styling, especially for SVGs.
                # Construct inline styles for the wrapper div and the image itself.
                style_parts = []
                if max_width:
                    style_parts.append(f"max-width: {max_width};")
                # Add other styles from config if needed (e.g., max-height, min-width)
                # if config.get("max_height"): style_parts.append(f"max-height: {config['max_height']};")
                # if config.get("min_width"): style_parts.append(f"min-width: {config['min_width']};")

                # Basic styles for centering and responsiveness
                div_style = (
                    " ".join(filter(None, style_parts))
                    + " margin: 1em auto; text-align: center;"
                )
                img_style = (
                    "max-width: 100%; height: auto; display: block; margin: 0 auto;"
                )

                # Create the HTML snippet
                replacement_text = (
                    f'\n\n<div style="{div_style.strip()}">\n'
                    f'    <img src="{relative_image_path}" alt="{alt_text}" style="{img_style}" />\n'
                    f"</div>\n\n"
                )
            else:
                # Use plain Markdown image syntax
                replacement_text = f"\n\n![{alt_text}]({relative_image_path})\n\n"

            successful_replacements += 1
            func_logger.debug(
                f"Replacing block {i+1} with image link: {relative_image_path}"
            )

        # --- Case 2: Conversion Failed ---
        else:
            # If conversion failed, keep the original Mermaid code block but wrap it
            # in an HTML comment to indicate the failure.
            warning_comment = f"\n"
            # Reconstruct the original block with fences
            original_block_formatted = f"```mermaid\n{block_text.strip()}\n```\n"
            # Combine comment and original block, adding newlines for separation
            replacement_text = (
                "\n\n" + warning_comment + original_block_formatted + "\n"
            )
            func_logger.warning(
                f"Keeping original code block {i+1} due to generation failure."
            )

        # --- Perform Replacement ---
        # Replace the original block slice with the generated replacement text
        new_content = new_content[:adj_start] + replacement_text + new_content[adj_end:]
        # Update the offset based on the difference in length between the replacement and original text
        offset += len(replacement_text) - (end_pos - start_pos)

    # Return the fully modified markdown content and the count of successful image replacements
    return new_content, successful_replacements


# --- Main Processing Function ---
def process_markdown_file(
    file_path,
    method="library",  # New: 'library' or 'kroki'
    kroki_url=None,  # New: URL for Kroki if method='kroki'
    **kwargs,  # Existing args like image_prefix, image_format, etc.
):
    """
    Processes a Markdown file to find Mermaid diagrams, convert them to images
    using the specified method ('library' or 'kroki'), and replace the code blocks
    with references to the generated images.

    Args:
        file_path (str): Path to the input Markdown file.
        method (str): Conversion method ('library' or 'kroki'). Defaults to 'library'.
        kroki_url (str, optional): URL of the Kroki instance if method is 'kroki'.
                                   Uses DEFAULT_KROKI_URL if None.
        **kwargs: Additional keyword arguments including:
            image_prefix (str): Prefix for generated image filenames. Default 'diagram'.
            image_format (str): Output image format ('svg' or 'png'). Default 'svg'.
            image_dir (str, optional): Specific directory for images. Default None (uses 'images/' subdir).
            diagram_config (dict, optional): Pre-loaded diagram configuration. Default None (loads automatically).
            config_path_input (str, optional): Path to config file if not using default. Default None.
            use_html_wrapper (bool): Whether to use HTML wrappers for images. Default True.
            output_suffix (str): Suffix for the output markdown filename. Default '-img'.

    Returns:
        dict: A dictionary containing processing statistics and results, including:
              'input_file_path', 'method_used', 'total_diagrams', 'successful_conversions',
              'failed_conversions', 'output_file_path' (potential path), 'image_directory',
              'all_conversions_successful' (bool), 'generated_image_paths' (list of abs paths),
              'new_content' (str), 'error' (str or None), 'telemetry' (dict).
    """
    func_logger = logging.getLogger(__name__)

    # --- Initialize Stats Dictionary ---
    # This dictionary will store all relevant information about the processing run.
    stats = {
        "input_file_path": os.path.abspath(file_path),  # Store absolute path
        "method_used": method,  # Record which method was used
        "total_diagrams": 0,  # Count of ```mermaid blocks found
        "successful_conversions": 0,  # Count of diagrams successfully converted to images
        "failed_conversions": 0,  # Count of diagrams that failed conversion
        "output_file_path": "",  # Potential path for the output .md file (calculated later)
        "image_directory": "",  # Absolute path to the directory where images are saved
        "all_conversions_successful": False,  # Flag, True only if all diagrams convert successfully
        "generated_image_paths": [],  # List of absolute paths to successfully generated images (for rollback)
        "new_content": "",  # The final generated markdown content string
        "error": None,  # Stores critical error messages that halt processing early
        "telemetry": {  # Performance and diagnostic data
            "start_time": time.time(),  # Timestamp when processing started
            "processing_times": [],  # List of durations (seconds) for each diagram conversion
            "total_runtime": 0,  # Total time taken for the whole function
            "avg_diagram_time": 0,  # Average time per successful conversion
            "image_sizes": [],  # List of file sizes (bytes) for successfully generated images
        },
    }

    # --- Pre-checks and Setup ---
    # 1. Validate chosen method and dependencies
    if method == "library" and not MERMAID_AVAILABLE:
        stats["error"] = (
            "Method 'library' selected, but python-mermaid library is not available or failed to import."
        )
        func_logger.critical(stats["error"])
        return stats  # Halt processing

    # 2. Load Diagram Configuration (if not provided)
    diagram_config = kwargs.get("diagram_config")
    if diagram_config is None:
        func_logger.debug(
            "Diagram config not provided in kwargs, loading default/from file."
        )
        # Use 'config_path_input' if passed from CLI/GUI, otherwise load_diagram_config uses its default logic
        diagram_config = load_diagram_config(kwargs.get("config_path_input"))

    # --- Input File Validation & Reading ---
    abs_file_path = stats["input_file_path"]
    func_logger.info(
        f"Starting processing for file: {abs_file_path} using method: {method}"
    )
    if not os.path.isfile(abs_file_path):
        stats["error"] = f"Input path is not a file or does not exist: {abs_file_path}"
        func_logger.error(stats["error"])
        return stats  # Halt processing

    try:
        # Read the entire content of the markdown file
        with open(abs_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        func_logger.debug(f"Successfully read input file: {abs_file_path}")
        # Store original content in case no diagrams are found or all fail
        stats["new_content"] = content
    except Exception as read_err:
        # Handle file reading errors
        stats["error"] = f"Failed to read input file {abs_file_path}: {read_err}"
        func_logger.error(stats["error"], exc_info=True)
        return stats  # Halt processing

    # --- Extract Mermaid Blocks ---
    mermaid_blocks = extract_mermaid_blocks(content)
    stats["total_diagrams"] = len(mermaid_blocks)
    # If no diagrams are found, processing is technically successful but no changes are made.
    if not mermaid_blocks:
        func_logger.info(
            f"No Mermaid diagrams found in {abs_file_path}. No conversion needed."
        )
        stats["all_conversions_successful"] = True
        # stats["new_content"] already contains original content
        stats["telemetry"]["total_runtime"] = (
            time.time() - stats["telemetry"]["start_time"]
        )
        return stats  # Return early, nothing more to do

    func_logger.info(f"Found {len(mermaid_blocks)} Mermaid diagram(s).")

    # --- Prepare Image Directory ---
    try:
        # Create or verify the directory where images will be saved
        abs_image_dir = create_image_directory(abs_file_path, kwargs.get("image_dir"))
        stats["image_directory"] = abs_image_dir
    except Exception as dir_err:
        # Handle errors during directory creation (e.g., permissions)
        stats["error"] = f"Failed to create image directory: {dir_err}"
        func_logger.error(stats["error"], exc_info=True)
        return stats  # Halt processing

    # --- Process Each Diagram ---
    # This list will store tuples of (relative_image_path, success_flag) for each block
    image_paths_info = []
    all_successful_flag = True  # Assume success until a failure occurs
    # Get the directory of the original markdown file for calculating relative paths
    output_md_dir = os.path.dirname(abs_file_path)

    # Loop through each extracted Mermaid block
    for i, (block_text, start_pos, end_pos) in enumerate(mermaid_blocks):
        diagram_index = i + 1  # Use 1-based indexing for user messages/filenames
        func_logger.info(
            f"--- Processing Diagram {diagram_index}/{len(mermaid_blocks)} ---"
        )

        # Generate a filename for the image
        image_name = create_image_name(
            prefix=kwargs.get(
                "image_prefix", "diagram"
            ),  # Use provided prefix or default
            index=diagram_index,
            mermaid_code=block_text,  # Use block text for hash generation
            image_format=kwargs.get(
                "image_format", "svg"
            ),  # Use provided format or default
        )
        # Construct the absolute path for the image file
        abs_image_path = os.path.join(abs_image_dir, image_name)

        # --- Call the Dispatcher to Generate the Image ---
        start_time = time.time()
        success = generate_diagram_image(
            method=method,  # Pass the chosen method
            mermaid_code=block_text,
            output_path=abs_image_path,
            image_format=kwargs.get("image_format", "svg"),
            kroki_url=kroki_url,  # Pass the Kroki URL (will be None if method is 'library')
        )
        end_time = time.time()
        # Record processing time for this diagram
        stats["telemetry"]["processing_times"].append(end_time - start_time)

        # --- Handle Generation Result ---
        if success:
            # Increment success counter
            stats["successful_conversions"] += 1
            # Add absolute path to list for potential rollback later
            stats["generated_image_paths"].append(abs_image_path)
            # Record image size
            try:
                stats["telemetry"]["image_sizes"].append(
                    os.path.getsize(abs_image_path)
                )
            except OSError:
                stats["telemetry"]["image_sizes"].append(
                    None
                )  # Record None if size cannot be read

            # Calculate the relative path from the output MD file's directory to the image
            try:
                rel_path = os.path.relpath(abs_image_path, start=output_md_dir).replace(
                    "\\", "/"
                )
            except ValueError:
                # This can happen on Windows if the image dir is on a different drive
                func_logger.warning(
                    f"Cannot create relative path for image {abs_image_path} from {output_md_dir}. Using file URI instead."
                )
                # Fallback to a file URI, which might work in some viewers
                rel_path = Path(abs_image_path).as_uri()
            # Store the relative path and success flag for replacement later
            image_paths_info.append((rel_path, True))
        else:
            # Increment failure counter
            stats["failed_conversions"] += 1
            # Mark that not all conversions were successful
            all_successful_flag = False
            # Store None for the path and False for the success flag
            image_paths_info.append((None, False))

    # Update the overall success flag in the stats
    stats["all_conversions_successful"] = all_successful_flag

    # --- Determine Potential Output Filename ---
    # Construct the name for the output markdown file based on the input name and suffix
    abs_file_path_obj = Path(abs_file_path)
    output_file_name = (
        f"{abs_file_path_obj.stem}{kwargs.get('output_suffix', '-img')}.md"
    )
    # Store the potential absolute path in the stats (file is not written here)
    stats["output_file_path"] = str(abs_file_path_obj.parent / output_file_name)

    # --- Generate the New Markdown Content String ---
    # This step happens regardless of individual diagram success/failure.
    # Failed blocks will be replaced with commented-out originals.
    func_logger.info("Generating final markdown content string...")
    new_content_str, successful_replacements = replace_mermaid_with_images_enhanced(
        markdown_content=content,  # Original content read earlier
        mermaid_blocks=mermaid_blocks,
        image_paths_info=image_paths_info,  # Results from the processing loop
        diagram_config=diagram_config,  # Pass the loaded config for styling
        use_html_wrapper=kwargs.get("use_html_wrapper", True),  # Pass flag from kwargs
    )
    # Store the final generated content in the stats dictionary
    stats["new_content"] = new_content_str

    # --- Compute Final Telemetry ---
    total_time = time.time() - stats["telemetry"]["start_time"]
    stats["telemetry"]["total_runtime"] = total_time
    # Calculate average time only if there were successful conversions
    if stats["successful_conversions"] > 0:
        # Filter out None values if any processing times failed to record (shouldn't happen often)
        valid_times = [
            t for t in stats["telemetry"]["processing_times"] if t is not None
        ]
        if valid_times:
            stats["telemetry"]["avg_diagram_time"] = sum(valid_times) / len(valid_times)

    # Log completion summary
    func_logger.info(
        f"Finished processing diagrams for {file_path}. Success: {all_successful_flag}. Total time: {total_time:.2f}s"
    )
    # Return the comprehensive statistics dictionary
    return stats


# --- Example Usage (if script is run directly) ---
if __name__ == "__main__":
    # Configure basic logging for standalone testing
    # This allows seeing logs when running `python converter.py`
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
    )
    logger.info(
        "Converter module loaded. Contains functions for processing markdown files."
    )
    print("Running standalone test examples...")

    # --- Create a dummy markdown file for testing ---
    test_md_content = """
# Test Document

This document tests the Mermaid conversion process.

## Flowchart Example

```mermaid
graph TD
    A[Start] --> B{Decision};
    B -- Yes --> C[Process 1];
    B -- No --> D[Process 2];
    C --> E[End];
    D --> E;
```

## Sequence Diagram Example

```mermaid
sequenceDiagram
    participant Alice
    participant Bob
    Alice->>+Bob: Hello Bob, how are you?
    Bob-->>-Alice: I am good thanks! How about you?
    Alice->>+Bob: Fine too!
```

## Example that might fail (e.g., invalid syntax)

```mermaid
graph TD
    X-- Y -- Z --
    Invalid Syntax Here
```
    """
    test_file_path = "test_converter_input.md"
    try:
        with open(test_file_path, "w", encoding="utf-8") as f:
            f.write(test_md_content)
        print(f"Created test file: {test_file_path}")

        # --- Run Test Cases ---
        print("\n" + "=" * 10 + " Testing LIBRARY method (SVG) " + "=" * 10)
        stats_lib_svg = process_markdown_file(
            test_file_path,
            method="library",
            image_format="svg",
            output_suffix="-lib-svg",  # Custom suffix for this test output
        )
        print(
            f"Library SVG Stats: Success={stats_lib_svg['all_conversions_successful']}, Successful={stats_lib_svg['successful_conversions']}, Failed={stats_lib_svg['failed_conversions']}"
        )
        if stats_lib_svg.get("new_content"):
            print(f"Output file would be: {stats_lib_svg.get('output_file_path')}")
            # Optionally write the output for inspection:
            # with open(stats_lib_svg['output_file_path'], "w", encoding='utf-8') as out_f: out_f.write(stats_lib_svg['new_content'])

        print("\n" + "=" * 10 + " Testing KROKI method (PNG) " + "=" * 10)
        print("INFO: Ensure Kroki is running, e.g., via Docker:")
        print("      docker run -d --rm -p 8000:8000 yuzutech/kroki")
        kroki_test_url = "http://localhost:8000"  # Use default or change if needed
        stats_kroki_png = process_markdown_file(
            test_file_path,
            method="kroki",
            kroki_url=kroki_test_url,
            image_format="png",
            output_suffix="-kroki-png",  # Custom suffix
        )
        print(
            f"Kroki PNG Stats: Success={stats_kroki_png['all_conversions_successful']}, Successful={stats_kroki_png['successful_conversions']}, Failed={stats_kroki_png['failed_conversions']}"
        )
        if stats_kroki_png.get("new_content"):
            print(f"Output file would be: {stats_kroki_png.get('output_file_path')}")
            # Optionally write the output for inspection:
            # with open(stats_kroki_png['output_file_path'], "w", encoding='utf-8') as out_f: out_f.write(stats_kroki_png['new_content'])

    finally:
        # --- Clean up test files/dirs ---
        print("\n--- Cleaning up ---")
        if os.path.exists(test_file_path):
            try:
                os.remove(test_file_path)
                print(f"Removed test file: {test_file_path}")
            except OSError as e:
                print(f"Error removing test file {test_file_path}: {e}")

        image_dir_path = "images"  # Default image dir used by tests
        if os.path.exists(image_dir_path):
            try:
                shutil.rmtree(image_dir_path)
                print(f"Removed test image directory: {image_dir_path}")
            except OSError as e:
                print(f"Error removing test image directory {image_dir_path}: {e}")
        # Clean up potential output files if they were written
        if (
            "stats_lib_svg" in locals()
            and stats_lib_svg.get("output_file_path")
            and os.path.exists(stats_lib_svg["output_file_path"])
        ):
            try:
                os.remove(stats_lib_svg["output_file_path"])
                print(f"Removed output: {stats_lib_svg['output_file_path']}")
            except OSError:
                pass
        if (
            "stats_kroki_png" in locals()
            and stats_kroki_png.get("output_file_path")
            and os.path.exists(stats_kroki_png["output_file_path"])
        ):
            try:
                os.remove(stats_kroki_png["output_file_path"])
                print(f"Removed output: {stats_kroki_png['output_file_path']}")
            except OSError:
                pass

    print("\nStandalone tests finished.")
