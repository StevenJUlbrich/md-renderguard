import argparse
import json
import logging
import os
import shutil  # Needed for helper function
import sys
import traceback

# --- Import Core Logic ---
try:
    from converter import load_diagram_config, process_markdown_file

    CONVERTER_AVAILABLE = True
except ImportError:
    print("ERROR: Failed to import 'converter' module.", file=sys.stderr)
    CONVERTER_AVAILABLE = False

    def load_diagram_config(p=None):
        return {}

    def process_markdown_file(**kwargs):
        return {"error": "Converter module not found."}

except Exception as import_err:
    print(
        f"ERROR: Unexpected error importing 'converter': {import_err}", file=sys.stderr
    )
    traceback.print_exc()
    CONVERTER_AVAILABLE = False

    def load_diagram_config(p=None):
        return {}

    def process_markdown_file(**kwargs):
        return {"error": "Converter import failed."}


# --- Constants ---
DEFAULT_CONFIG_FILENAME = "diagram_config.json"
DEFAULT_OUTPUT_SUFFIX = "-img"
LOG_FILENAME = "mermaid_converter.log"
MERMAID_VERSION_DIR = "mermaid_version"  # Subdirectory name


# --- Logger Setup ---
# (Setup function remains the same as previous version v2)
def setup_logger():
    """Configures the root logger for command-line usage."""
    root_logger = logging.getLogger()
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) for h in root_logger.handlers
    )
    has_file_handler = any(
        isinstance(h, logging.FileHandler)
        and hasattr(h, "baseFilename")
        and os.path.normpath(h.baseFilename) == os.path.normpath(LOG_FILENAME)
        for h in root_logger.handlers
    )
    if has_stream_handler and has_file_handler and root_logger.level != logging.NOTSET:
        return
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
            root_logger.removeHandler(handler)
        except Exception:
            pass
    root_logger.setLevel(logging.DEBUG)
    log_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s] %(message)s"
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    try:
        file_handler = logging.FileHandler(LOG_FILENAME, encoding="utf-8", mode="a")
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)
        root_logger.debug(
            f"Root logger configured: Console (INFO+), File ('{LOG_FILENAME}', DEBUG+)."
        )
    except Exception as e:
        root_logger.error(
            f"Failed to create log file handler for {LOG_FILENAME}: {e}", exc_info=True
        )
        print(
            f"Error: Could not open log file {LOG_FILENAME}. Logging to console only.",
            file=sys.stderr,
        )


# --- Helper Functions ---
# (create_default_config remains the same as previous version v2)
def create_default_config(output_path):
    """Creates a default diagram configuration JSON file."""
    logger = logging.getLogger(__name__)
    default_config = {
        "default": {"max_width": "600px"},
        "flowchart": {"max_width": "650px"},
        # Add other types...
    }
    abs_output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(abs_output_path)
    logger.info(f"Attempting to create default config file at: {abs_output_path}")
    try:
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            logger.debug(f"Ensured directory exists: {output_dir}")
        with open(abs_output_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)
        logger.info(f"Successfully created default config file: {abs_output_path}")
        print(f"Default configuration file created at: {abs_output_path}")
        return True
    except OSError as dir_err:
        logger.error(
            f"Failed to create directory for config file {abs_output_path}: {dir_err}",
            exc_info=True,
        )
        print(
            f"Error: Could not create directory for {abs_output_path}", file=sys.stderr
        )
        return False
    except Exception as e:
        logger.error(
            f"Error creating config file at {abs_output_path}: {e}", exc_info=True
        )
        print(
            f"Error: Could not create config file at {abs_output_path}: {e}",
            file=sys.stderr,
        )
        return False


# --- CLI Specific Helper Functions ---
def _cli_write_output_file(output_path, content):
    """Writes content to the specified output file path (CLI version)."""
    logger = logging.getLogger(__name__)  # Use logger from main scope
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Successfully created output file: {output_path}")
        return True
    except Exception as write_err:
        logger.error(
            f"Failed to write output file {output_path}: {write_err}", exc_info=True
        )
        print(f"Error: Failed to write output file {output_path}", file=sys.stderr)
        return False


def _cli_move_original_and_readme(
    original_path, move_dest_dir, add_readme_flag, output_file_name, image_format
):
    """Creates dest dir, moves original file, optionally adds readme (CLI version)."""
    logger = logging.getLogger(__name__)  # Use logger from main scope
    original_moved = False
    readme_added = False
    try:
        os.makedirs(move_dest_dir, exist_ok=True)
        logger.info(
            f"Ensured '{MERMAID_VERSION_DIR}' directory exists: {move_dest_dir}"
        )

        original_filename = os.path.basename(original_path)
        move_dest_path = os.path.join(move_dest_dir, original_filename)

        logger.info(
            f"Attempting to move original file '{original_path}' to '{move_dest_path}'"
        )
        shutil.move(original_path, move_dest_path)
        logger.info(f"Successfully moved original file to: {move_dest_path}")
        original_moved = True

        if add_readme_flag:
            readme_path = os.path.join(move_dest_dir, "readme.md")
            output_md_dir = os.path.dirname(original_path)
            readme_content = (
                f"This folder contains the original version ('{original_filename}') ...\n"  # Content as before
                f"A converted version ... name '{output_file_name}'."
            )
            try:
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(readme_content)
                logger.info(f"Successfully created readme.md in {move_dest_dir}")
                readme_added = True
            except Exception as readme_err:
                logger.error(
                    f"Failed to create readme.md in {move_dest_dir}: {readme_err}",
                    exc_info=True,
                )
                print(
                    f"Warning: Failed to create readme.md in {move_dest_dir}",
                    file=sys.stderr,
                )

    except OSError as move_os_err:
        logger.error(
            f"Failed to create directory '{move_dest_dir}': {move_os_err}",
            exc_info=True,
        )
        print(f"Error: Failed to create directory '{move_dest_dir}'", file=sys.stderr)
    except Exception as move_err:
        logger.error(
            f"Failed to move original file '{original_path}' to '{move_dest_dir}': {move_err}",
            exc_info=True,
        )
        print(f"Error: Failed to move original file '{original_path}'", file=sys.stderr)
        original_moved = False

    return original_moved, readme_added


def _cli_rollback_images(image_paths):
    """Deletes the list of generated image files during rollback (CLI version)."""
    logger = logging.getLogger(__name__)  # Use logger from main scope
    logger.warning("Rolling back changes: Deleting generated images...")
    deleted_count = 0
    for img_path in image_paths:
        try:
            if os.path.isfile(img_path):
                os.remove(img_path)
                logger.info(f"Deleted image during rollback: {img_path}")
                deleted_count += 1
            else:
                logger.warning(f"Image file not found during rollback: {img_path}")
        except OSError as del_err:
            logger.error(
                f"Failed to delete image during rollback {img_path}: {del_err}",
                exc_info=True,
            )
            print(
                f"Warning: Failed to delete image {img_path} during rollback.",
                file=sys.stderr,
            )
    logger.warning(
        f"Rollback complete. Deleted {deleted_count}/{len(image_paths)} generated images."
    )
    return deleted_count


# --- Main Execution Logic ---
def main():
    """Main entry point for the command-line interface."""
    setup_logger()
    logger = logging.getLogger(__name__)  # Logger for main function
    logger.debug("CLI Logger configured and main logger instance obtained.")

    # --- Argument Parser Setup ---
    # (Parser setup remains the same as previous version v2)
    parser = argparse.ArgumentParser(
        description="Convert Mermaid diagrams within Markdown files into linked images (SVG or PNG).",
        epilog=f"Example: python main.py report.md --format png --output-suffix -png --move-original --add-readme",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "file",
        nargs="?",
        metavar="MARKDOWN_FILE",
        help="Path to the input Markdown file (.md). Required unless using --gui or --create-config.",
    )
    parser.add_argument(
        "--prefix", "-p", default="diagram", help="Prefix for generated image files."
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["svg", "png"],
        default="svg",
        help="Image format for diagrams.",
    )
    parser.add_argument(
        "--image-dir",
        "-i",
        metavar="DIR",
        help="Custom directory for images. Default: 'images/' subdir.",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        metavar="CONFIG_JSON",
        help=f"Path to JSON config file. Default: '{DEFAULT_CONFIG_FILENAME}' near script.",
    )
    parser.add_argument(
        "--output-suffix",
        "-s",
        default=DEFAULT_OUTPUT_SUFFIX,
        help="Suffix for output markdown filename.",
    )
    parser.add_argument(
        "--markdown-style",
        "-m",
        action="store_true",
        help="Use standard Markdown image syntax `![alt](path)`.",
    )
    parser.add_argument(
        "--move-original",
        action="store_true",
        help=f"Move original file to '{MERMAID_VERSION_DIR}/' on success.",
    )
    parser.add_argument(
        "--add-readme",
        action="store_true",
        help=f"Add readme.md to '{MERMAID_VERSION_DIR}/' (requires --move-original).",
    )
    parser.add_argument(
        "--gui", action="store_true", help="Launch the graphical user interface (GUI)."
    )
    parser.add_argument(
        "--create-config",
        metavar="OUTPUT_PATH",
        help=f"Create default config file and exit.",
        nargs="?",
        const=DEFAULT_CONFIG_FILENAME,
    )

    # --- Parse Arguments ---
    try:
        args = parser.parse_args()
        logger.debug(f"Parsed arguments: {args}")
    except Exception as parse_err:
        logger.error(f"Error parsing arguments: {parse_err}", exc_info=True)
        sys.exit(2)

    # --- Handle Actions ---
    # 1. Create Config
    if args.create_config is not None:
        logger.info(
            f"Action: Create default config requested at '{args.create_config}'"
        )
        success = create_default_config(args.create_config)
        sys.exit(0 if success else 1)

    # 2. GUI Action
    if args.gui:
        if args.file:
            logger.warning("Ignoring specified input file because --gui flag was used.")
        logger.info("Action: Launching GUI...")
        try:
            import tkinter

            from gui import main as gui_main

            gui_main()
            logger.info("GUI closed.")
            sys.exit(0)
        except ImportError as import_err:
            logger.critical(
                f"GUI launch failed (Import Error): {import_err}", exc_info=True
            )
            print(
                "Error: Failed to launch GUI. Ensure tkinter is installed and gui.py is accessible.",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as gui_err:
            logger.critical(
                f"GUI launch failed (Runtime Error): {gui_err}", exc_info=True
            )
            print(f"Error: Unexpected problem starting GUI: {gui_err}", file=sys.stderr)
            sys.exit(1)

    # 3. File Processing Action (Default)
    if not args.file:
        parser.error("Input MARKDOWN_FILE is required for processing.")

    input_file_path = args.file
    logger.info(f"Action: Processing input file '{input_file_path}' via CLI.")

    # Validate Input File
    abs_input_file_path = os.path.abspath(input_file_path)
    if not os.path.isfile(abs_input_file_path):
        logger.critical(f"Input file not found: {abs_input_file_path}")
        print(f"Error: Input file not found: {abs_input_file_path}", file=sys.stderr)
        sys.exit(1)
    if not abs_input_file_path.lower().endswith(".md"):
        logger.warning(
            f"Input file '{abs_input_file_path}' may not be Markdown (.md extension missing)."
        )

    # Check Converter Availability
    if not CONVERTER_AVAILABLE:
        logger.critical("Core converter logic failed to load. Cannot process file.")
        print(
            "Error: Cannot process file because 'converter' module failed.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Config
    diagram_config = load_diagram_config(args.config)
    config_source_desc = f"'{args.config}'" if args.config else "default"
    logger.debug(f"Using diagram configuration loaded from {config_source_desc}.")

    # Determine Image Directory
    image_directory = args.image_dir
    if image_directory:
        image_directory = os.path.abspath(image_directory)
        logger.info(f"Using image directory: {image_directory}")
    else:
        logger.info("Using default image directory ('images/' subdir).")

    # --- Execute File Processing ---
    logger.info("Calling process_markdown_file...")
    stats = {}  # Initialize stats dict
    try:
        stats = process_markdown_file(
            file_path=abs_input_file_path,
            image_prefix=args.prefix,
            image_format=args.format,
            image_dir=image_directory,
            diagram_config=diagram_config,
            use_html_wrapper=(not args.markdown_style),
            output_suffix=args.output_suffix,
        )

        # --- Handle Results ---
        exit_code = 1  # Default to error exit code
        final_message = (
            "\nProcessing failed unexpectedly. Check logs."  # Default message
        )

        # Check for early critical errors returned by converter
        if stats.get("error"):
            logger.error(f"Conversion failed early: {stats['error']}")
            final_message = f"\nError: Could not process file: {stats['error']}"

        # Check if all conversions were successful
        elif stats.get("all_conversions_successful"):
            logger.info(
                "All diagrams converted successfully. Performing file operations..."
            )
            # Write the output file
            output_written = _cli_write_output_file(
                stats["output_file_path"], stats["new_content"]
            )

            stats["original_moved"] = False
            stats["readme_added"] = False
            stats["move_dest_dir"] = ""
            if output_written and args.move_original:
                original_path = stats["input_file_path"]
                output_md_dir = os.path.dirname(original_path)
                move_dest_dir = os.path.join(output_md_dir, MERMAID_VERSION_DIR)
                output_filename = os.path.basename(stats["output_file_path"])

                moved, readme = _cli_move_original_and_readme(
                    original_path,
                    move_dest_dir,
                    args.add_readme,
                    output_filename,
                    args.format,
                )
                stats["original_moved"] = moved
                stats["readme_added"] = readme
                stats["move_dest_dir"] = move_dest_dir if moved else ""
            elif not output_written:
                logger.error(
                    "Skipping move/readme because output file failed to write."
                )
            elif not args.move_original:
                logger.info("Skipping move/readme because it was not requested.")

            # Set success message and exit code
            final_message = "\nConversion completed successfully."
            if stats.get("original_moved"):
                final_message += " Original file moved."
            exit_code = 0

        # --- Handle Failure: Automatic Rollback for CLI ---
        else:
            failed_count = stats.get("failed_conversions", "Some")
            logger.warning(
                f"{failed_count} diagram(s) failed conversion. Rolling back changes for CLI."
            )
            print(
                f"\nWARNING: {failed_count} diagram(s) failed conversion. Rolling back changes.",
                file=sys.stderr,
            )
            _cli_rollback_images(stats.get("generated_image_paths", []))
            stats["rolled_back"] = True  # Mark rollback in stats
            # Do not write output file, do not move original
            final_message = (
                "\nConversion failed. Changes rolled back (generated images deleted)."
            )
            exit_code = 1  # Indicate failure

        # --- Print Final Summary ---
        print("\n--- Conversion Summary ---")
        print(
            f"Input File:           {stats.get('input_file_path', abs_input_file_path)}"
        )
        print(
            f"Output File:          {stats.get('output_file_path', 'N/A') if not stats.get('rolled_back') else 'N/A (Rolled Back)'}"
        )
        print(f"Image Directory:      {stats.get('image_directory', 'N/A')}")
        print(f"Diagrams Found:       {stats.get('total_diagrams', 0)}")
        print(f"Successful Converts:  {stats.get('successful_conversions', 0)}")
        print(f"Failed Converts:      {stats.get('failed_conversions', 0)}")
        if stats.get("rolled_back"):
            print(f"Rolled Back:          Yes")
        if args.move_original and not stats.get(
            "rolled_back"
        ):  # Show move only if attempted and not rolled back
            moved_status = "Yes" if stats.get("original_moved") else "No"
            dest_dir = (
                f"to '{stats.get('move_dest_dir', 'N/A')}'"
                if stats.get("original_moved")
                else "(Check logs)"
            )
            print(f"Original File Moved:  {moved_status} {dest_dir}")
            if stats.get("original_moved") and args.add_readme:
                readme_status = (
                    "Yes" if stats.get("readme_added") else "No (Check logs)"
                )
                print(f"Readme Added:         {readme_status}")

        print(final_message)  # Print the final status message
        sys.exit(exit_code)  # Exit with appropriate code

    except Exception as proc_err:
        # Catch unexpected errors during the main processing call
        logger.critical(
            f"An unexpected error occurred during file processing: {proc_err}",
            exc_info=True,
        )
        print(f"\nError: An unexpected problem occurred: {proc_err}", file=sys.stderr)
        print(f"Check the log file ('{LOG_FILENAME}') for details.", file=sys.stderr)
        sys.exit(1)


# --- Script Execution Guard ---
if __name__ == "__main__":
    try:
        main()
    except Exception as top_level_err:
        print(f"\nFATAL ERROR: {top_level_err}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
