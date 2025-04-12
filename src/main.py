import argparse
import json
import logging
import os
import shutil
import sys
import traceback
from pathlib import Path

# --- Import Core Logic ---
# We now primarily need process_markdown_file from the updated converter
try:
    from converter import (
        CONVERTER_AVAILABLE,
    )  # General check if converter module loaded
    from converter import (
        MERMAID_AVAILABLE,
    )  # Check if library is available for validation
    from converter import (
        load_diagram_config,
    )  # Still needed for --create-config potentially
    from converter import process_markdown_file

    # Note: We no longer need to import the specific generation functions here
except ImportError:
    print(
        "ERROR: Failed to import 'converter' module. Cannot continue.", file=sys.stderr
    )
    # Define dummy functions to prevent NameErrors if import fails, though script should exit
    CONVERTER_AVAILABLE = False
    MERMAID_AVAILABLE = False

    def process_markdown_file(**kwargs):
        return {
            "error": "Converter module not found.",
            "all_conversions_successful": False,
        }

    def load_diagram_config(p=None):
        return {}

except Exception as import_err:
    print(
        f"ERROR: Unexpected error importing 'converter': {import_err}", file=sys.stderr
    )
    traceback.print_exc()
    CONVERTER_AVAILABLE = False
    MERMAID_AVAILABLE = False

    def process_markdown_file(**kwargs):
        return {
            "error": "Converter import failed.",
            "all_conversions_successful": False,
        }

    def load_diagram_config(p=None):
        return {}


# --- Constants ---
DEFAULT_CONFIG_FILENAME = "diagram_config.json"
DEFAULT_OUTPUT_SUFFIX = "-img"  # Default suffix for the output markdown file
LOG_FILENAME = "mermaid_converter.log"  # Log file name
MERMAID_VERSION_DIR = "mermaid_version"  # Subdirectory for moved original files
DEFAULT_KROKI_URL = "http://localhost:8000"  # Default Kroki instance URL


# --- Logger Setup ---
def setup_logger():
    """Configures the root logger for command-line usage."""
    root_logger = logging.getLogger()
    # Avoid reconfiguring if already set up (e.g., if called multiple times)
    if (
        any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
        and any(
            isinstance(h, logging.FileHandler)
            and os.path.normpath(getattr(h, "baseFilename", ""))
            == os.path.normpath(LOG_FILENAME)
            for h in root_logger.handlers
        )
        and root_logger.level != logging.NOTSET
    ):
        return root_logger  # Already configured

    # Remove existing handlers to prevent duplicates if re-run in same process
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
            root_logger.removeHandler(handler)
        except Exception:
            pass  # Ignore errors during handler removal

    root_logger.setLevel(
        logging.DEBUG
    )  # Set root logger level (handlers control output)
    log_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s] %(message)s"
    )

    # Console Handler (INFO level)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # File Handler (DEBUG level)
    try:
        file_handler = logging.FileHandler(
            LOG_FILENAME, encoding="utf-8", mode="a"
        )  # Append mode
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)
        root_logger.debug(
            f"Root logger configured: Console (INFO+), File ('{LOG_FILENAME}', DEBUG+)."
        )
    except Exception as e:
        # Fallback if log file cannot be created
        root_logger.error(
            f"Failed to create log file handler for {LOG_FILENAME}: {e}", exc_info=True
        )
        print(
            f"Error: Could not open log file {LOG_FILENAME}. Logging to console only.",
            file=sys.stderr,
        )

    return root_logger


# --- Helper Functions ---
def create_default_config(output_path):
    """Creates a default diagram configuration JSON file."""
    # This function remains independent and uses its own logger instance if needed
    func_logger = logging.getLogger(__name__ + ".create_default_config")
    # Use the same default config structure as defined in converter.py's load_diagram_config
    default_config = {
        "default": {"max_width": "600px"},
        "flowchart": {"max_width": "650px"},
        "sequence": {"max_width": "550px"},
        # Add other types if desired in the default file...
    }
    abs_output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(abs_output_path)
    func_logger.info(f"Attempting to create default config file at: {abs_output_path}")
    try:
        # Ensure the target directory exists
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            func_logger.debug(f"Ensured directory exists: {output_dir}")
        # Write the default config as JSON
        with open(abs_output_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)  # Use indent for readability
        func_logger.info(f"Successfully created default config file: {abs_output_path}")
        print(f"Default configuration file created at: {abs_output_path}")
        return True
    except OSError as dir_err:
        # Handle directory creation errors
        func_logger.error(
            f"Failed to create directory for config file {abs_output_path}: {dir_err}",
            exc_info=True,
        )
        print(
            f"Error: Could not create directory for {abs_output_path}", file=sys.stderr
        )
        return False
    except Exception as e:
        # Handle file writing or other errors
        func_logger.error(
            f"Error creating config file at {abs_output_path}: {e}", exc_info=True
        )
        print(
            f"Error: Could not create config file at {abs_output_path}: {e}",
            file=sys.stderr,
        )
        return False


# --- CLI Specific Helper Functions (Write Output, Move Original, Rollback Images) ---
# These functions handle file operations specific to the CLI workflow based on the
# results returned by process_markdown_file.


def _cli_write_output_file(output_path, content):
    """Writes the processed markdown content to the specified output file."""
    cli_logger = logging.getLogger(__name__)  # Use main logger
    cli_logger.info(f"Attempting to write output file: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        cli_logger.info(f"Successfully created output file: {output_path}")
        return True
    except Exception as write_err:
        cli_logger.error(
            f"Failed to write output file {output_path}: {write_err}", exc_info=True
        )
        print(f"Error: Failed to write output file {output_path}", file=sys.stderr)
        return False


def _cli_move_original_and_readme(
    original_path, move_dest_dir, add_readme_flag, output_file_name, image_format
):
    """
    Moves the original markdown file to a subdirectory and optionally adds a README.md.
    """
    cli_logger = logging.getLogger(__name__)
    original_moved = False
    readme_added = False
    try:
        # Ensure the destination directory (e.g., 'mermaid_version/') exists
        os.makedirs(move_dest_dir, exist_ok=True)
        cli_logger.info(
            f"Ensured '{os.path.basename(move_dest_dir)}' directory exists: {move_dest_dir}"
        )

        # Construct the destination path for the original file
        original_filename = os.path.basename(original_path)
        move_dest_path = os.path.join(move_dest_dir, original_filename)

        # Move the file
        cli_logger.info(
            f"Attempting to move original file '{original_path}' to '{move_dest_path}'"
        )
        shutil.move(original_path, move_dest_path)
        cli_logger.info(f"Successfully moved original file to: {move_dest_path}")
        original_moved = True

        # Add README.md if requested and the move was successful
        if add_readme_flag:
            readme_path = os.path.join(move_dest_dir, "readme.md")
            # Get original directory for context in readme
            output_md_dir = os.path.dirname(original_path)
            # Define README content
            readme_content = (
                f"This folder contains the original version ('{original_filename}') of a Markdown file "
                "that included Mermaid diagrams.\n\n"
                "The file was moved here because Mermaid diagrams may not render correctly "
                "in all Markdown viewers or platforms.\n\n"
                "A converted version of the file, with Mermaid diagrams rendered as images "
                f"('{image_format.upper()}'), should be located in the parent directory ('{output_md_dir}') "
                f"with the name '{output_file_name}'."
            )
            try:
                # Write the README file
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(readme_content)
                cli_logger.info(f"Successfully created readme.md in {move_dest_dir}")
                readme_added = True
            except Exception as readme_err:
                # Log error if README creation fails, but don't stop the process
                cli_logger.error(
                    f"Failed to create readme.md in {move_dest_dir}: {readme_err}",
                    exc_info=True,
                )
                print(
                    f"Warning: Failed to create readme.md in {move_dest_dir}",
                    file=sys.stderr,
                )
                # readme_added remains False

    except OSError as move_os_err:
        # Error creating directory
        cli_logger.error(
            f"Failed to create directory '{move_dest_dir}': {move_os_err}",
            exc_info=True,
        )
        print(f"Error: Failed to create directory '{move_dest_dir}'", file=sys.stderr)
    except Exception as move_err:
        # Error during the actual file move operation
        cli_logger.error(
            f"Failed to move original file '{original_path}' to '{move_dest_dir}': {move_err}",
            exc_info=True,
        )
        print(f"Error: Failed to move original file '{original_path}'", file=sys.stderr)
        original_moved = False  # Ensure flag is false if move failed

    return original_moved, readme_added


def _cli_rollback_images(image_paths):
    """Deletes the list of generated image files during rollback."""
    cli_logger = logging.getLogger(__name__)
    cli_logger.warning("Rolling back changes: Deleting generated images...")
    deleted_count = 0
    if not image_paths:
        cli_logger.warning(
            "Rollback requested, but no images were generated or tracked."
        )
        return 0  # Nothing to delete

    # Iterate through the list of absolute image paths provided
    for img_path in image_paths:
        try:
            # Check if the file exists before attempting deletion
            if os.path.isfile(img_path):
                os.remove(img_path)
                cli_logger.info(f"Deleted image during rollback: {img_path}")
                deleted_count += 1
            else:
                # Log if a file expected to be deleted was not found
                cli_logger.warning(
                    f"Image file not found during rollback (already deleted?): {img_path}"
                )
        except OSError as del_err:
            # Log errors during deletion (e.g., permission issues)
            cli_logger.error(
                f"Failed to delete image during rollback {img_path}: {del_err}",
                exc_info=True,
            )
            print(
                f"Warning: Failed to delete image {img_path} during rollback.",
                file=sys.stderr,
            )

    cli_logger.warning(
        f"Rollback complete. Deleted {deleted_count}/{len(image_paths)} generated images."
    )
    return deleted_count


# --- Main Execution Logic ---
def main():
    """Main entry point for the command-line interface."""
    # Setup logger for the application run
    logger = setup_logger()
    logger.debug("CLI Logger configured and main logger instance obtained.")

    # --- Argument Parser Setup ---
    parser = argparse.ArgumentParser(
        description="Convert Mermaid diagrams within Markdown files into linked images (SVG or PNG) using either the python-mermaid library or a Kroki instance.",
        epilog=(
            f"Example (Library): python {os.path.basename(__file__)} report.md --converter library\n"
            f"Example (Kroki):   python {os.path.basename(__file__)} report.md --converter kroki --kroki-url http://mykroki:8000 --format png"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,  # Show default values in help
    )
    # Input/Output Args
    parser.add_argument(
        "file",
        nargs="?",  # Makes the argument optional
        metavar="MARKDOWN_FILE",
        help="Path to the input Markdown file (.md). Required unless using --gui or --create-config.",
    )
    parser.add_argument(
        "--output-suffix",
        "-s",
        default=DEFAULT_OUTPUT_SUFFIX,
        help="Suffix to append to the input filename for the output markdown file.",
    )
    # Converter Choice Args
    parser.add_argument(
        "--converter",
        choices=["library", "kroki"],
        default="library",  # Default to using the python-mermaid library
        help="Choose the conversion method: 'library' (requires python-mermaid) or 'kroki' (requires a running Kroki HTTP API instance).",
    )
    parser.add_argument(
        "--kroki-url",
        default=DEFAULT_KROKI_URL,
        metavar="URL",
        help="URL of the Kroki instance (only used if --converter=kroki).",
    )
    # Image Formatting Args
    parser.add_argument(
        "--prefix",
        "-p",
        default="diagram",
        help="Prefix for generated image filenames (e.g., 'diagram-1-hash.svg').",
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
        help="Custom directory for storing generated images. Default: Creates an 'images/' subdirectory relative to the input markdown file.",
    )
    # Configuration Args
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        metavar="CONFIG_JSON",
        help=(
            f"Path to a JSON config file for diagram styling hints (e.g., max_width). "
            f"Default: Looks for '{DEFAULT_CONFIG_FILENAME}' near this script."
        ),
    )
    # Output Style Args
    parser.add_argument(
        "--markdown-style",
        "-m",
        action="store_true",  # Makes it a flag, default is False
        help="Use standard Markdown image syntax `![alt](path)` instead of the default HTML `<div><img>...` wrapper (HTML wrapper allows better styling).",
    )
    # File Management Args
    parser.add_argument(
        "--move-original",
        action="store_true",
        help=f"Move the original markdown file to a '{MERMAID_VERSION_DIR}/' subdirectory upon successful conversion.",
    )
    parser.add_argument(
        "--add-readme",
        action="store_true",
        help=f"Add a 'readme.md' file explaining the move to the '{MERMAID_VERSION_DIR}/' subdirectory (only effective if --move-original is also used).",
    )
    # Mode Args
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the graphical user interface (GUI) instead of processing a file via CLI.",
    )
    parser.add_argument(
        "--create-config",
        metavar="OUTPUT_PATH",
        help=f"Create a default '{DEFAULT_CONFIG_FILENAME}' file at the specified path (or in the current directory if no path given) and exit.",
        nargs="?",  # Argument is optional
        const=DEFAULT_CONFIG_FILENAME,  # Value if flag is given with no argument
    )

    # --- Parse Arguments ---
    try:
        args = parser.parse_args()
        logger.debug(f"Parsed arguments: {args}")
    except Exception as parse_err:
        # Handle errors during argument parsing (e.g., invalid choices)
        logger.error(f"Error parsing arguments: {parse_err}", exc_info=True)
        # parser.print_usage() # Optionally show usage
        sys.exit(2)  # Standard exit code for command line syntax errors

    # --- Handle Special Actions (Create Config, GUI) ---
    # 1. Create Config Action
    if args.create_config is not None:
        logger.info(
            f"Action: Create default config requested at '{args.create_config}'"
        )
        success = create_default_config(args.create_config)
        sys.exit(0 if success else 1)  # Exit after creating config

    # 2. GUI Action
    if args.gui:
        # Ignore file argument if --gui is used
        if args.file:
            logger.warning("Ignoring specified input file because --gui flag was used.")
        logger.info("Action: Launching GUI...")
        try:
            # Check for tkinter availability before attempting to import the GUI module
            import tkinter
        except ImportError:
            logger.critical("GUI launch failed: tkinter library not found.")
            print(
                "Error: Failed to launch GUI. The tkinter library is required but not found.",
                file=sys.stderr,
            )
            sys.exit(1)  # Dependency error
        try:
            # Import and run the GUI's main function
            from gui import main as gui_main

            gui_main()  # This will typically block until the GUI window is closed
            logger.info("GUI closed.")
            sys.exit(0)  # Exit successfully after GUI closes
        except ImportError as import_err:
            # Handle error if gui.py itself cannot be imported
            logger.critical(
                f"GUI launch failed (Import Error): {import_err}", exc_info=True
            )
            print(
                f"Error: Failed to launch GUI. Could not import 'gui' module: {import_err}",
                file=sys.stderr,
            )
            print(
                "Ensure gui.py is in the same directory or Python path.",
                file=sys.stderr,
            )
            sys.exit(1)  # Import error
        except Exception as gui_err:
            # Handle unexpected errors during GUI execution
            logger.critical(
                f"GUI launch failed (Runtime Error): {gui_err}", exc_info=True
            )
            print(
                f"Error: Unexpected problem starting or running the GUI: {gui_err}",
                file=sys.stderr,
            )
            sys.exit(1)  # Runtime error

    # --- File Processing Action (Default CLI mode) ---
    # If we reach here, it's CLI processing mode. Check for required file argument.
    if not args.file:
        parser.error(
            "Input MARKDOWN_FILE is required for CLI processing (or use --gui or --create-config)."
        )

    input_file_path = args.file
    logger.info(
        f"Action: Processing input file '{input_file_path}' via CLI using '{args.converter}' converter."
    )

    # Validate Input File Path
    abs_input_file_path = os.path.abspath(input_file_path)
    if not os.path.isfile(abs_input_file_path):
        logger.critical(f"Input file not found: {abs_input_file_path}")
        print(f"Error: Input file not found: {abs_input_file_path}", file=sys.stderr)
        sys.exit(1)  # File not found error
    # Optional: Warn if not a .md file
    if not abs_input_file_path.lower().endswith(".md"):
        logger.warning(
            f"Input file '{abs_input_file_path}' may not be Markdown (.md extension missing)."
        )

    # Check Core Converter Availability
    if not CONVERTER_AVAILABLE:
        # This check is slightly redundant if imports failed earlier, but good practice
        logger.critical(
            "Core converter logic ('converter.py') failed to load. Cannot process file."
        )
        print(
            "Error: Cannot process file because the 'converter' module failed to load.",
            file=sys.stderr,
        )
        sys.exit(1)  # Dependency error

    # Check Specific Library Availability if chosen
    if args.converter == "library" and not MERMAID_AVAILABLE:
        logger.critical(
            "Converter set to 'library', but the python-mermaid library failed to load or is not installed."
        )
        print(
            "Error: The python-mermaid library (required for --converter=library) is not available.",
            file=sys.stderr,
        )
        print("Please install it, e.g., 'pip install python-mermaid'", file=sys.stderr)
        sys.exit(1)  # Dependency error

    # --- Execute File Processing via Converter Module ---
    logger.info("Calling process_markdown_file from converter module...")
    stats = {}  # Initialize stats dict
    try:
        # Call the main processing function from converter.py, passing all relevant args
        stats = process_markdown_file(
            file_path=abs_input_file_path,
            method=args.converter,
            kroki_url=args.kroki_url,  # Pass Kroki URL (used only if method='kroki')
            image_prefix=args.prefix,
            image_format=args.format,
            image_dir=args.image_dir,  # Pass custom image dir or None
            config_path_input=args.config,  # Pass path to config file or None
            use_html_wrapper=(
                not args.markdown_style
            ),  # Pass inverse of markdown_style flag
            output_suffix=args.output_suffix,
            # diagram_config could be pre-loaded here, but letting converter handle it is fine
        )

        # --- Handle Results from process_markdown_file ---
        exit_code = 1  # Default to error exit code
        final_message = (
            "\nProcessing failed unexpectedly. Check logs."  # Default message
        )
        rolled_back = False  # Flag for rollback status

        # Check for critical errors reported by the converter
        if stats.get("error"):
            logger.error(f"Conversion failed early: {stats['error']}")
            final_message = f"\nError: Could not process file: {stats['error']}"
            # No files should have been written or moved in case of early error

        # Check if all conversions were successful
        elif stats.get("all_conversions_successful"):
            logger.info(
                "All diagrams converted successfully. Performing file operations..."
            )
            # Write the output file using the content generated by the converter
            output_written = _cli_write_output_file(
                stats["output_file_path"], stats["new_content"]
            )

            # Handle moving the original file and adding README if requested
            original_moved = False
            readme_added = False
            move_dest_dir_path = ""  # Store path for summary message
            if output_written and args.move_original:
                # Determine the destination directory for the move
                output_md_dir = os.path.dirname(abs_input_file_path)
                move_dest_dir_path = os.path.join(output_md_dir, MERMAID_VERSION_DIR)
                # Get the base name of the output file for the README
                output_filename_base = os.path.basename(stats["output_file_path"])
                # Perform the move and readme creation
                original_moved, readme_added = _cli_move_original_and_readme(
                    abs_input_file_path,  # Path to the original input file
                    move_dest_dir_path,
                    args.add_readme,
                    output_filename_base,
                    args.format,  # Pass image format for README content
                )
            elif not output_written:
                logger.error(
                    "Skipping move/readme because output file failed to write."
                )
            elif not args.move_original:
                logger.info(
                    "Skipping move/readme because --move-original was not specified."
                )

            # Set success message and exit code
            final_message = "\nConversion completed successfully."
            if original_moved:
                final_message += f" Original file moved to '{move_dest_dir_path}'."
            if readme_added:
                final_message += " README added."
            exit_code = 0  # Success exit code

        # --- Handle Partial Failure: Automatic Rollback for CLI ---
        else:
            # This block executes if process_markdown_file indicated some failures
            failed_count = stats.get("failed_conversions", "Some")
            logger.warning(
                f"{failed_count} diagram(s) failed conversion. Rolling back changes for CLI."
            )
            print(
                f"\nWARNING: {failed_count} diagram(s) failed conversion. Rolling back changes.",
                file=sys.stderr,
            )
            # Delete any images that were successfully generated during the partial run
            _cli_rollback_images(stats.get("generated_image_paths", []))
            rolled_back = True  # Mark that rollback occurred
            # Do not write the output file, do not move the original
            final_message = (
                "\nConversion failed. Changes rolled back (generated images deleted)."
            )
            exit_code = 1  # Indicate failure

        # --- Print Final Summary ---
        print("\n--- Conversion Summary ---")
        print(
            f"Input File:           {stats.get('input_file_path', abs_input_file_path)}"
        )
        print(f"Converter Used:       {stats.get('method_used', args.converter)}")
        if stats.get("method_used") == "kroki":
            print(
                f"Kroki URL:            {args.kroki_url}"
            )  # Show URL if Kroki was used
        print(
            f"Output File:          {stats.get('output_file_path', 'N/A') if not rolled_back and exit_code == 0 else 'N/A (Not Created or Rolled Back)'}"
        )
        print(f"Image Directory:      {stats.get('image_directory', 'N/A')}")
        print(f"Diagrams Found:       {stats.get('total_diagrams', 0)}")
        print(f"Successful Converts:  {stats.get('successful_conversions', 0)}")
        print(f"Failed Converts:      {stats.get('failed_conversions', 0)}")
        print(f"Rolled Back:          {'Yes' if rolled_back else 'No'}")
        # Show move/readme status only if attempted and not rolled back
        if args.move_original and not rolled_back and exit_code == 0:
            moved_status = "Yes" if original_moved else "No"
            dest_dir_display = (
                f"to '{move_dest_dir_path}'"
                if original_moved
                else "(Check logs for errors)"
            )
            print(f"Original File Moved:  {moved_status} {dest_dir_display}")
            if original_moved and args.add_readme:
                readme_status = "Yes" if readme_added else "No (Check logs for errors)"
                print(f"Readme Added:         {readme_status}")

        print(final_message)  # Print the final status message
        sys.exit(exit_code)  # Exit with appropriate code

    except Exception as proc_err:
        # Catch unexpected errors during the main processing call or post-processing
        logger.critical(
            f"An unexpected error occurred during file processing: {proc_err}",
            exc_info=True,
        )
        print(f"\nError: An unexpected problem occurred: {proc_err}", file=sys.stderr)
        print(f"Check the log file ('{LOG_FILENAME}') for details.", file=sys.stderr)
        # Attempt rollback if the error occurred after some images might have been created
        if stats and stats.get("generated_image_paths"):
            print(
                "Attempting to roll back any generated images due to unexpected error...",
                file=sys.stderr,
            )
            _cli_rollback_images(stats.get("generated_image_paths"))
        sys.exit(1)  # General runtime error


# --- Script Execution Guard ---
if __name__ == "__main__":
    # Ensure Path is available if needed, though it's imported globally now
    # from pathlib import Path
    try:
        main()
    except SystemExit as e:
        # Allow SystemExit to propagate (used by argparse and successful exits)
        raise e
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        print("\nOperation cancelled by user (KeyboardInterrupt).", file=sys.stderr)
        # Perform any necessary cleanup here if needed
        sys.exit(130)  # Standard exit code for Ctrl+C
    except Exception as top_level_err:
        # Catch any other exceptions that might occur outside the main() try block
        print(
            f"\nFATAL ERROR: An unexpected error occurred: {top_level_err}",
            file=sys.stderr,
        )
        traceback.print_exc()
        # Attempt to log the fatal error if logger was set up
        try:
            logging.getLogger(__name__).critical(
                f"FATAL ERROR: {top_level_err}", exc_info=True
            )
        except Exception:
            pass
        sys.exit(1)  # Critical error exit code
