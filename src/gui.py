import json
import logging
import os
import queue
import shutil  # Needed for moving files
import sys
import threading
import tkinter as tk
import traceback
from tkinter import (
    Label,
    Toplevel,
    filedialog,
    messagebox,  # For tooltips
    scrolledtext,
    ttk,
)

# --- Attempt to Import Core Logic ---
# We need process_markdown_file and potentially load_diagram_config

# Define the flag locally BEFORE the try block, default to False
CONVERTER_AVAILABLE = False
MERMAID_AVAILABLE = False  # This will be set by the imported value if successful
DEFAULT_KROKI_URL = "http://localhost:8000"  # Fallback default

try:
    # Import necessary functions and constants from converter.py
    # *** REMOVED CONVERTER_AVAILABLE FROM THIS IMPORT LIST ***
    from converter import (
        DEFAULT_KROKI_URL,
    )  # Import default Kroki URL (defined in converter.py)
    from converter import (
        MERMAID_AVAILABLE,
    )  # Check if library is available (defined in converter.py)
    from converter import load_diagram_config  # Used for 'Edit Config'/'Create Default'
    from converter import process_markdown_file

    # If the import above succeeds, set the local flag to True
    CONVERTER_AVAILABLE = True

except ImportError:
    # Handle case where converter.py itself cannot be found/imported
    print("ERROR: Failed to import 'converter' module.", file=sys.stderr)

    # CONVERTER_AVAILABLE remains False (set before try block)
    # MERMAID_AVAILABLE remains False (set before try block)
    # Define dummy functions for limited GUI functionality
    def load_diagram_config(p=None):
        print("Warning: converter module missing, cannot load config.", file=sys.stderr)
        messagebox.showerror(
            "Error",
            "Core 'converter.py' module not found.\nFunctionality will be limited.",
        )
        return {}

    def process_markdown_file(**kwargs):
        print("Error: converter module missing, cannot process file.", file=sys.stderr)
        messagebox.showerror(
            "Error", "Core 'converter.py' module not found.\nCannot process file."
        )
        return {
            "error": "Converter module not found.",
            "all_conversions_successful": False,
        }

except Exception as import_err:
    # Handle other unexpected errors during the import process
    print(
        f"ERROR: Unexpected error importing 'converter': {import_err}", file=sys.stderr
    )
    traceback.print_exc()

    # CONVERTER_AVAILABLE remains False
    # MERMAID_AVAILABLE remains False
    # Define dummy functions
    def load_diagram_config(p=None):
        return {}

    def process_markdown_file(**kwargs):
        return {
            "error": "Converter import failed.",
            "all_conversions_successful": False,
        }


# --- Constants ---
DEFAULT_OUTPUT_SUFFIX = "-img"  # Default suffix for output markdown file name
DEFAULT_CONFIG_FILENAME = "diagram_config.json"  # Default config filename
APP_TITLE = "Mermaid Markdown Converter"  # Application window title
MERMAID_VERSION_DIR = "mermaid_version"  # Subdirectory name for moved original files

# --- Logging Setup ---
log_queue = queue.Queue()  # Thread-safe queue for log messages from background tasks
logger = logging.getLogger()  # Get the root logger instance


class QueueHandler(logging.Handler):
    """
    Custom logging handler that puts log records into a thread-safe Queue.
    The GUI thread will periodically check this queue to display log messages.
    """

    def __init__(self, log_queue_instance):
        super().__init__()
        self.log_queue = log_queue_instance
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record):
        """Puts the log record into the queue."""
        record.handler = self
        self.log_queue.put(record)


def setup_gui_logger():
    """
    Configures the root logger specifically for the GUI.
    Removes existing handlers and adds a QueueHandler.
    """
    for handler in logger.handlers[:]:
        try:
            handler.close()
            logger.removeHandler(handler)
        except Exception as e:
            print(f"Warning: Error removing logger handler: {e}", file=sys.stderr)

    queue_handler = QueueHandler(log_queue)
    gui_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s] %(message)s", datefmt="%H:%M:%S"
    )
    queue_handler.setFormatter(gui_formatter)
    logger.addHandler(queue_handler)
    logger.setLevel(logging.INFO)


def check_dependencies():
    """
    Checks if core dependencies seem available. Returns list of issues.
    Uses the locally defined CONVERTER_AVAILABLE and the imported MERMAID_AVAILABLE.
    """
    func_logger = logging.getLogger(__name__ + ".check_dependencies")
    missing = []
    # Check if the converter module itself loaded successfully (using the local flag)
    if not CONVERTER_AVAILABLE:
        missing.append(
            "converter.py - Failed to load core module (check console errors)."
        )

    # Check if the python-mermaid library is available (using the flag imported from converter.py)
    # This check is only fully meaningful if CONVERTER_AVAILABLE is True
    if CONVERTER_AVAILABLE and not MERMAID_AVAILABLE:
        missing.append(
            "python-mermaid (package) - Not found or failed to import (required for 'Library' method)."
        )

    # Check for Tkinter
    try:
        import tkinter
    except ImportError:
        missing.append(
            "tkinter - GUI library not found (required for this application)."
        )

    # Check for requests (needed for Kroki)
    try:
        import requests
    except ImportError:
        missing.append("requests (package) - Not found (required for 'Kroki' method).")

    func_logger.debug(f"Dependency check results: {missing or 'OK'}")
    return missing


# --- Helper Functions for File Operations ---
# (These functions remain the same)
def _write_output_file(output_path, content):
    func_logger = logging.getLogger(__name__)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        func_logger.info(f"Successfully created output file: {output_path}")
        return True
    except Exception as write_err:
        func_logger.error(
            f"Failed to write output file {output_path}: {write_err}", exc_info=True
        )
        return False


def _move_original_and_readme(
    original_path, move_dest_dir, add_readme_flag, output_file_name, image_format
):
    func_logger = logging.getLogger(__name__)
    original_moved = False
    readme_added = False
    try:
        os.makedirs(move_dest_dir, exist_ok=True)
        func_logger.info(
            f"Ensured '{os.path.basename(move_dest_dir)}' directory exists: {move_dest_dir}"
        )
        original_filename = os.path.basename(original_path)
        move_dest_path = os.path.join(move_dest_dir, original_filename)
        func_logger.info(
            f"Attempting to move original file '{original_path}' to '{move_dest_path}'"
        )
        shutil.move(original_path, move_dest_path)
        func_logger.info(f"Successfully moved original file to: {move_dest_path}")
        original_moved = True
        if add_readme_flag:
            readme_path = os.path.join(move_dest_dir, "readme.md")
            output_md_dir = os.path.dirname(original_path)
            readme_content = (
                f"This folder contains the original version ('{original_filename}') ...\n"  # (content same as before)
                f"A converted version ... name '{output_file_name}'."
            )
            try:
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(readme_content)
                func_logger.info(f"Successfully created readme.md in {move_dest_dir}")
                readme_added = True
            except Exception as readme_err:
                func_logger.error(
                    f"Failed to create readme.md in {move_dest_dir}: {readme_err}",
                    exc_info=True,
                )
    except OSError as move_os_err:
        func_logger.error(
            f"Failed to create directory '{move_dest_dir}': {move_os_err}",
            exc_info=True,
        )
    except Exception as move_err:
        func_logger.error(
            f"Failed to move original file '{original_path}' to '{move_dest_dir}': {move_err}",
            exc_info=True,
        )
        original_moved = False
    return original_moved, readme_added


def _rollback_images(image_paths):
    func_logger = logging.getLogger(__name__)
    func_logger.warning("Rolling back changes: Deleting generated images...")
    deleted_count = 0
    if not image_paths:
        func_logger.warning("Rollback requested, but no images were generated.")
        return 0
    for img_path in image_paths:
        try:
            if os.path.isfile(img_path):
                os.remove(img_path)
                func_logger.info(f"Deleted image during rollback: {img_path}")
                deleted_count += 1
            else:
                func_logger.warning(f"Image file not found during rollback: {img_path}")
        except OSError as del_err:
            func_logger.error(
                f"Failed to delete image during rollback {img_path}: {del_err}",
                exc_info=True,
            )
    func_logger.warning(
        f"Rollback complete. Deleted {deleted_count}/{len(image_paths)} generated images."
    )
    return deleted_count


# --- Tooltip Helper ---
def create_tooltip(widget, text):
    tooltip = None

    def enter(event):
        nonlocal tooltip
        x, y, _, _ = widget.bbox("insert")
        x += widget.winfo_rootx() + 25
        y += widget.winfo_rooty() + 25
        tooltip = Toplevel(widget)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{x}+{y}")
        label = Label(
            tooltip,
            text=text,
            background="#FFFFCC",
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=2,
        )
        label.pack()

    def leave(event):
        nonlocal tooltip
        if tooltip:
            tooltip.destroy()
            tooltip = None

    widget.bind("<Enter>", enter)
    widget.bind("<Leave>", leave)


# --- Main GUI Application Class ---
class MermaidConverterGUI:
    """Encapsulates the Tkinter GUI application."""

    def __init__(self, root_window):
        """Initialize the GUI application window and widgets."""
        self.root = root_window
        self.root.title(APP_TITLE)
        self.root.geometry("850x750")
        self.root.minsize(700, 600)

        setup_gui_logger()
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing GUI...")

        # --- Theming & Styles ---
        self.style = ttk.Style()
        try:
            available_themes = self.style.theme_names()
            preferred_themes = ["clam", "vista", "xpnative", "alt", "default"]
            for theme in preferred_themes:
                if theme in available_themes:
                    try:
                        self.style.theme_use(theme)
                        self.logger.debug(f"Using theme: {theme}")
                        break
                    except tk.TclError:
                        self.logger.debug(f"Theme '{theme}' failed, trying next.")
        except tk.TclError as e:
            self.logger.warning(f"Theme error: {e}. Using default.")
        font_family = "Segoe UI"
        font_size = 10
        self.style.configure("TLabel", font=(font_family, font_size))
        self.style.configure("TButton", font=(font_family, font_size), padding=5)
        self.style.configure("TEntry", font=(font_family, font_size), padding=3)
        self.style.configure("TCombobox", font=(font_family, font_size))
        self.style.configure("TCheckbutton", font=(font_family, font_size))
        self.style.configure("TRadiobutton", font=(font_family, font_size))
        self.style.configure("TLabelframe.Label", font=(font_family, font_size, "bold"))
        self.style.configure("Status.TLabel", font=(font_family, font_size - 1))

        # --- Main Frame ---
        self.main_frame = ttk.Frame(root_window, padding="15")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame.rowconfigure(3, weight=1)
        self.main_frame.columnconfigure(0, weight=1)

        # --- Tkinter Variables ---
        self.file_path_var = tk.StringVar()
        self.image_prefix_var = tk.StringVar(value="diagram")
        self.image_format_var = tk.StringVar(value="svg")
        self.image_dir_var = tk.StringVar()
        self.config_file_var = tk.StringVar()
        self.output_suffix_var = tk.StringVar(value=f"-{self.image_format_var.get()}")
        self.use_markdown_style_var = tk.BooleanVar(value=False)
        self.move_original_var = tk.BooleanVar(value=False)
        self.add_readme_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready.")
        self.is_processing = False
        self.converter_method_var = tk.StringVar(value="library")  # Default method
        self.kroki_url_var = tk.StringVar(
            value=DEFAULT_KROKI_URL
        )  # Use imported default

        # Set default config path
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            default_config_path = os.path.join(script_dir, DEFAULT_CONFIG_FILENAME)
            self.config_file_var.set(default_config_path)
            self.logger.debug(f"Default config path set: {default_config_path}")
        except NameError:
            self.config_file_var.set(DEFAULT_CONFIG_FILENAME)
            self.logger.debug(
                f"Default config path fallback: {DEFAULT_CONFIG_FILENAME}"
            )

        # --- Create UI Sections ---
        self.create_file_selection_frame().grid(
            row=0, column=0, sticky="ew", pady=(0, 10)
        )
        self.create_options_frame().grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.create_action_buttons_frame().grid(
            row=2, column=0, sticky="ew", pady=(5, 15)
        )
        self.create_log_frame().grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        self.create_status_bar().pack(side=tk.BOTTOM, fill=tk.X)

        # --- Initial Setup ---
        self.check_and_log_dependencies()
        self.process_log_queue()
        self._on_converter_method_change()  # Set initial state
        self._on_move_original_toggle()  # Set initial state
        self.logger.info("GUI Initialized and Ready.")

    # --- UI Creation Methods ---
    # (create_file_selection_frame remains the same)
    def create_file_selection_frame(self):
        frame = ttk.LabelFrame(self.main_frame, text="Input File", padding="10")
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Markdown File:").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )
        entry = ttk.Entry(frame, textvariable=self.file_path_var, width=60)
        entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        button = ttk.Button(frame, text="Browse...", command=self.browse_file)
        button.grid(row=0, column=2, padx=(5, 0), pady=5)
        return frame

    def create_options_frame(self):
        """Creates the frame containing various conversion options."""
        frame = ttk.LabelFrame(self.main_frame, text="Options", padding="10")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        current_row = 0

        # --- Converter Method Selection ---
        ttk.Label(frame, text="Converter Method:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        method_frame = ttk.Frame(frame)
        method_frame.grid(
            row=current_row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2
        )
        self.library_radio = ttk.Radiobutton(
            method_frame,
            text="Library (python-mermaid)",
            variable=self.converter_method_var,
            value="library",
            command=self._on_converter_method_change,
        )
        self.library_radio.pack(side=tk.LEFT, padx=(0, 10))
        create_tooltip(self.library_radio, "Use installed python-mermaid library.")
        self.kroki_radio = ttk.Radiobutton(
            method_frame,
            text="Kroki (HTTP API)",
            variable=self.converter_method_var,
            value="kroki",
            command=self._on_converter_method_change,
        )
        self.kroki_radio.pack(side=tk.LEFT)
        create_tooltip(self.kroki_radio, "Use a running Kroki instance.")
        current_row += 1

        # --- Kroki URL ---
        ttk.Label(frame, text="Kroki Server URL:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.kroki_url_entry = ttk.Entry(
            frame, textvariable=self.kroki_url_var, width=50
        )
        self.kroki_url_entry.grid(
            row=current_row, column=1, columnspan=3, padx=5, pady=5, sticky=tk.EW
        )
        create_tooltip(
            self.kroki_url_entry,
            "URL of your running Kroki instance (e.g., http://localhost:8000).",
        )
        current_row += 1

        # --- Separator ---
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=current_row, column=0, columnspan=4, sticky="ew", pady=10
        )
        current_row += 1

        # --- Image Prefix, Format ---
        ttk.Label(frame, text="Image Prefix:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        prefix_entry = ttk.Entry(frame, textvariable=self.image_prefix_var, width=20)
        prefix_entry.grid(row=current_row, column=1, padx=5, pady=5, sticky=tk.W)
        create_tooltip(prefix_entry, "Prefix for image filenames.")
        ttk.Label(frame, text="Image Format:").grid(
            row=current_row, column=2, sticky=tk.W, padx=(15, 5), pady=5
        )
        format_combo = ttk.Combobox(
            frame,
            textvariable=self.image_format_var,
            values=["svg", "png"],
            width=8,
            state="readonly",
        )
        format_combo.grid(row=current_row, column=3, padx=5, pady=5, sticky=tk.W)
        format_combo.bind("<<ComboboxSelected>>", self._update_output_suffix)
        create_tooltip(format_combo, "Output image format.")
        current_row += 1

        # --- Output Suffix ---
        ttk.Label(frame, text="Output Suffix:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        suffix_entry = ttk.Entry(
            frame, textvariable=self.output_suffix_var, width=15, state="readonly"
        )
        suffix_entry.grid(row=current_row, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(frame, text="(Auto-set by Format)").grid(
            row=current_row, column=2, columnspan=2, sticky=tk.W, padx=5, pady=0
        )
        current_row += 1

        # --- Image Directory ---
        ttk.Label(frame, text="Image Directory:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        img_dir_entry = ttk.Entry(frame, textvariable=self.image_dir_var, width=50)
        img_dir_entry.grid(
            row=current_row, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW
        )
        ttk.Button(frame, text="Browse...", command=self.browse_directory).grid(
            row=current_row, column=3, padx=5, pady=5, sticky=tk.W
        )
        create_tooltip(
            img_dir_entry, "Optional: Directory for images. Default: 'images/' subdir."
        )
        current_row += 1
        ttk.Label(frame, text="(Optional. Default: 'images/' subdir)").grid(
            row=current_row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=(0, 5)
        )
        current_row += 1

        # --- Config File ---
        ttk.Label(frame, text="Config JSON:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        config_entry = ttk.Entry(frame, textvariable=self.config_file_var, width=50)
        config_entry.grid(
            row=current_row, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW
        )
        ttk.Button(frame, text="Browse...", command=self.browse_config).grid(
            row=current_row, column=3, padx=5, pady=5, sticky=tk.W
        )
        create_tooltip(config_entry, "Optional: JSON file with styling hints.")
        current_row += 1
        config_button_frame = ttk.Frame(frame)
        config_button_frame.grid(
            row=current_row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=(0, 5)
        )
        ttk.Button(
            config_button_frame,
            text="Create Default",
            command=self.create_default_config_file,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            config_button_frame, text="Edit", command=self.edit_config_file
        ).pack(side=tk.LEFT)
        current_row += 1

        # --- Separator ---
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=current_row, column=0, columnspan=4, sticky="ew", pady=10
        )
        current_row += 1

        # --- Output Style ---
        md_style_cb = ttk.Checkbutton(
            frame,
            text="Use standard Markdown image syntax (![alt](path))",
            variable=self.use_markdown_style_var,
        )
        md_style_cb.grid(
            row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=5
        )
        create_tooltip(md_style_cb, "Use ![alt](path) instead of HTML wrapper.")
        current_row += 1

        # --- Move Original ---
        move_cb = ttk.Checkbutton(
            frame,
            text=f"Move original file to '{MERMAID_VERSION_DIR}/' folder",
            variable=self.move_original_var,
            command=self._on_move_original_toggle,
        )
        move_cb.grid(
            row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=5
        )
        create_tooltip(move_cb, "Move original file on success.")
        current_row += 1

        # --- Add README ---
        self.add_readme_checkbox = ttk.Checkbutton(
            frame,
            text=f"Add 'readme.md' to '{MERMAID_VERSION_DIR}/' folder (requires move)",
            variable=self.add_readme_var,
        )
        self.add_readme_checkbox.grid(
            row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=(0, 5)
        )
        create_tooltip(
            self.add_readme_checkbox, "Add explanatory README if moving original."
        )

        return frame

    # (create_action_buttons_frame remains the same)
    def create_action_buttons_frame(self):
        frame = ttk.Frame(self.main_frame)
        quit_btn = ttk.Button(
            frame, text="Quit", command=self._quit_application, width=10
        )
        quit_btn.pack(side=tk.RIGHT, padx=(10, 0), pady=5)
        self.convert_button = ttk.Button(
            frame, text="Convert File", command=self.start_conversion, width=20
        )
        self.convert_button.pack(side=tk.RIGHT, pady=5)
        return frame

    # (create_log_frame remains the same)
    def create_log_frame(self):
        frame = ttk.LabelFrame(self.main_frame, text="Log Output", padding="10")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            height=10,
            font=("Consolas", 9),
            relief=tk.SUNKEN,
            borderwidth=1,
            state=tk.DISABLED,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("INFO", foreground="black")
        self.log_text.tag_configure("WARNING", foreground="#E69900")
        self.log_text.tag_configure("ERROR", foreground="red")
        self.log_text.tag_configure(
            "CRITICAL", foreground="red", font=("Consolas", 9, "bold")
        )
        self.log_text.tag_configure("DEBUG", foreground="gray50")
        return frame

    # (create_status_bar remains the same)
    def create_status_bar(self):
        status_bar = ttk.Frame(self.root, relief=tk.GROOVE, padding=2)
        self.progress = ttk.Progressbar(
            status_bar, orient=tk.HORIZONTAL, mode="indeterminate", length=150
        )
        self.status_label = ttk.Label(
            status_bar, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2, padx=5)
        return status_bar

    # --- Event Handlers and Actions ---
    # (check_and_log_dependencies remains the same)
    def check_and_log_dependencies(self):
        missing_deps = check_dependencies()
        if missing_deps:
            self.logger.error("--- Dependency Issues Detected ---")
            [self.logger.error(f"  - {dep}") for dep in missing_deps]
            self.logger.error("Functionality may be limited or fail.")
            if "tkinter" not in str(missing_deps).lower():
                messagebox.showwarning(
                    "Dependency Warning",
                    "Potential dependency issues found:\n\n- "
                    + "\n- ".join(missing_deps)
                    + "\n\nPlease check requirements.",
                    parent=self.root,
                )
        else:
            self.logger.info("Dependency check passed.")

    # (_update_output_suffix remains the same)
    def _update_output_suffix(self, event=None):
        selected_format = self.image_format_var.get()
        new_suffix = f"-{selected_format}"
        self.output_suffix_var.set(new_suffix)
        self.logger.debug(f"Output suffix auto-updated: {new_suffix}")

    # (_on_move_original_toggle remains the same)
    def _on_move_original_toggle(self):
        state = tk.NORMAL if self.move_original_var.get() else tk.DISABLED
        self.add_readme_checkbox.config(state=state)
        if state == tk.DISABLED:
            self.add_readme_var.set(False)
        self.logger.debug(
            f"Move original: {self.move_original_var.get()}, Readme state: {state}"
        )

    # (_on_converter_method_change remains the same)
    def _on_converter_method_change(self):
        state = tk.NORMAL if self.converter_method_var.get() == "kroki" else tk.DISABLED
        self.kroki_url_entry.config(state=state)
        self.logger.debug(
            f"Converter method: {self.converter_method_var.get()}, Kroki URL state: {state}"
        )

    # (_quit_application remains the same)
    def _quit_application(self):
        self.logger.info("Quit button clicked.")
        if self.is_processing:
            self.logger.warning("Quit during active processing.")
        logging.shutdown()
        self.root.destroy()

    # --- File/Directory Browsing Methods ---
    # (browse_file, browse_directory, browse_config remain the same)
    def browse_file(self):
        file_path = filedialog.askopenfilename(
            title="Select Markdown File",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
            parent=self.root,
        )
        if file_path:
            self.file_path_var.set(file_path)
            self.logger.info(f"Input file selected: {file_path}")

    def browse_directory(self):
        dir_path = filedialog.askdirectory(
            title="Select Custom Image Directory (Optional)", parent=self.root
        )
        if dir_path:
            self.image_dir_var.set(dir_path)
            self.logger.info(f"Custom image dir selected: {dir_path}")

    def browse_config(self):
        file_path = filedialog.askopenfilename(
            title="Select Config JSON (Optional)",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            parent=self.root,
        )
        if file_path:
            self.config_file_var.set(file_path)
            self.logger.info(f"Config file selected: {file_path}")

    # --- Config File Actions ---
    # (create_default_config_file, edit_config_file remain the same)
    def create_default_config_file(self):
        file_path = filedialog.asksaveasfilename(
            title="Save Default Config As...",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=DEFAULT_CONFIG_FILENAME,
            parent=self.root,
        )
        if file_path:
            try:
                success = create_default_config(file_path)  # Use helper
                if success:
                    self.config_file_var.set(os.path.abspath(file_path))
                    messagebox.showinfo(
                        "Success",
                        f"Default config saved:\n{file_path}",
                        parent=self.root,
                    )
            except Exception as e:
                self.logger.error(f"Error creating default config: {e}", exc_info=True)
                messagebox.showerror(
                    "Error", f"Could not save config:\n{e}", parent=self.root
                )

    def edit_config_file(self):
        config_path = self.config_file_var.get()
        if not config_path:
            messagebox.showwarning(
                "Edit Config", "Select or create config file first.", parent=self.root
            )
            return
        abs_config_path = os.path.abspath(config_path)
        if not os.path.isfile(abs_config_path):
            if messagebox.askyesno(
                "File Not Found",
                f"Config file not found:\n{abs_config_path}\n\nCreate default one here?",
                parent=self.root,
            ):
                self.config_file_var.set(abs_config_path)
                self.create_default_config_file()
            return
        try:
            self.logger.info(f"Opening '{abs_config_path}' in default editor...")
            if sys.platform.startswith("win"):
                os.startfile(abs_config_path)
            elif sys.platform.startswith("darwin"):
                os.system(f'open "{abs_config_path}"')
            else:
                os.system(f'xdg-open "{abs_config_path}"')
        except Exception as e:
            self.logger.error(f"Error opening config file: {e}", exc_info=True)
            messagebox.showerror(
                "Error", f"Could not open config file:\n{e}", parent=self.root
            )

    # --- Logging and Status Updates ---
    # (log_message_to_gui, process_log_queue remain the same)
    def log_message_to_gui(self, message, level=logging.INFO):
        if not hasattr(self, "log_text") or not self.log_text.winfo_exists():
            return
        try:
            self.log_text.config(state=tk.NORMAL)
            level_name = logging.getLevelName(level)
            self.log_text.insert(
                tk.END, message + ("" if message.endswith("\n") else "\n"), level_name
            )
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"GUI Log Error: {e}", file=sys.stderr)

    def process_log_queue(self):
        try:
            while True:
                record = log_queue.get_nowait()
                formatted_msg = (
                    record.handler.formatter.format(record)
                    if hasattr(record, "handler")
                    and hasattr(record.handler, "formatter")
                    else f"{record.levelname}: {record.getMessage()}"
                )
                if hasattr(self, "root") and self.root.winfo_exists():
                    self.root.after(
                        0, self.log_message_to_gui, formatted_msg, record.levelno
                    )
                if hasattr(record, "handler"):
                    del record.handler
        except queue.Empty:
            pass
        except Exception as e:
            print(f"Error processing log queue: {e}", file=sys.stderr)
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.after(100, self.process_log_queue)

    # --- Conversion Process Management ---
    def start_conversion(self):
        """Validates inputs and starts the conversion process in a background thread."""
        self.logger.debug("Convert button clicked.")
        # --- Input Validation --- (Now includes checks for method/dependencies)
        if self.is_processing:
            messagebox.showwarning(
                "Busy", "Conversion already in progress.", parent=self.root
            )
            return
        file_path = self.file_path_var.get()
        if not file_path:
            messagebox.showerror(
                "Input Error", "Please select input file.", parent=self.root
            )
            return
        abs_file_path = os.path.abspath(file_path)
        if not os.path.isfile(abs_file_path):
            messagebox.showerror(
                "Input Error",
                f"Input file not found:\n{abs_file_path}",
                parent=self.root,
            )
            return
        image_prefix = self.image_prefix_var.get()
        invalid_chars = r'<>:"/\|?*'
        if any(c in invalid_chars for c in image_prefix):
            messagebox.showerror(
                "Invalid Input",
                f"Image prefix invalid.\nAvoid: {invalid_chars}",
                parent=self.root,
            )
            return
        if not CONVERTER_AVAILABLE or process_markdown_file is None:
            messagebox.showerror(
                "Critical Error", "Core converter module not loaded.", parent=self.root
            )
            return

        selected_method = self.converter_method_var.get()
        if selected_method == "library" and not MERMAID_AVAILABLE:
            messagebox.showerror(
                "Dependency Error",
                "'Library' method requires python-mermaid package.",
                parent=self.root,
            )
            return
        if selected_method == "kroki":
            try:
                import requests
            except ImportError:
                messagebox.showerror(
                    "Dependency Error",
                    "'Kroki' method requires 'requests' package.",
                    parent=self.root,
                )
                return
            kroki_url_val = self.kroki_url_var.get() or DEFAULT_KROKI_URL
            if not kroki_url_val.startswith(("http://", "https://")):
                messagebox.showerror(
                    "Input Error",
                    f"Invalid Kroki URL format:\n{kroki_url_val}",
                    parent=self.root,
                )
                return

        # --- Prepare GUI ---
        try:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass
        self.logger.info("=" * 25 + " Starting Conversion " + "=" * 25)
        self.convert_button.config(state=tk.DISABLED)
        self.progress.pack(side=tk.LEFT, padx=(5, 10), pady=2)
        self.progress.start(10)
        self.status_var.set("Processing... Please wait.")
        self.is_processing = True

        # --- Gather Options --- (Includes method and kroki_url)
        options = {
            "method": selected_method,  # Get selected method
            "kroki_url": self.kroki_url_var.get() or None,  # Get Kroki URL
            "image_prefix": self.image_prefix_var.get() or "diagram",
            "image_format": self.image_format_var.get(),
            "image_dir": self.image_dir_var.get() or None,
            "config_path_input": self.config_file_var.get() or None,
            "use_html_wrapper": not self.use_markdown_style_var.get(),
            "output_suffix": self.output_suffix_var.get(),
            "move_original_requested": self.move_original_var.get(),
            "add_readme_requested": self.add_readme_var.get(),
        }

        # --- Start Background Thread ---
        self.logger.info(
            f"Starting conversion in background thread (Method: {selected_method})..."
        )
        conversion_thread = threading.Thread(
            target=self.run_conversion_thread,
            args=(abs_file_path, options),
            daemon=True,
        )
        conversion_thread.start()

    def run_conversion_thread(self, abs_file_path, options):
        """Worker function executed in background. Calls process_markdown_file."""
        self.logger.debug(
            f"Background thread started for {os.path.basename(abs_file_path)}"
        )
        stats = {}
        try:
            # *** Call the updated process_markdown_file from converter.py ***
            stats = process_markdown_file(
                file_path=abs_file_path,
                method=options["method"],  # Pass method
                kroki_url=options["kroki_url"],  # Pass kroki_url
                image_prefix=options["image_prefix"],
                image_format=options["image_format"],
                image_dir=options["image_dir"],
                config_path_input=options["config_path_input"],
                use_html_wrapper=options["use_html_wrapper"],
                output_suffix=options["output_suffix"],
            )
            # Add back flags needed only by GUI thread for file ops
            stats["move_original_requested"] = options["move_original_requested"]
            stats["add_readme_requested"] = options["add_readme_requested"]
            stats["image_format"] = options["image_format"]  # Needed for readme content
            self.logger.debug(
                "process_markdown_file completed. Scheduling GUI result handler."
            )
        except Exception as e:
            thread_error_msg = f"Unexpected error during conversion: {str(e)}"
            self.logger.critical(thread_error_msg, exc_info=True)
            stats["error"] = thread_error_msg
            stats["all_conversions_successful"] = False
            stats.setdefault("input_file_path", abs_file_path)  # Ensure keys exist
            stats.setdefault(
                "move_original_requested", options["move_original_requested"]
            )
            stats.setdefault("add_readme_requested", options["add_readme_requested"])
            stats.setdefault("image_format", options["image_format"])
        finally:
            # Schedule GUI update
            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after(0, self.handle_conversion_result, stats)
            else:
                self.logger.warning("GUI closed before conversion thread finished.")

    # (handle_conversion_result remains the same logic, uses stats dict)
    def handle_conversion_result(self, stats):
        self.logger.debug("Running handle_conversion_result callback.")
        if stats.get("error") and not stats.get("total_diagrams", 0) > 0:
            self.logger.error(f"Conversion failed early: {stats['error']}")
            self._reset_ui_state()
            messagebox.showerror(
                "Conversion Failed",
                f"Could not process file:\n{stats['error']}",
                parent=self.root,
            )
            return

        all_successful = stats.get("all_conversions_successful", False)
        proceed_action = False
        if all_successful:
            self.logger.info("All diagrams converted successfully.")
            proceed_action = True
        else:
            failed_count = stats.get("failed_conversions", "Some")
            error_msg = stats.get("error", f"{failed_count} diagram(s) failed.")
            self.logger.warning(f"{error_msg}. Prompting user.")
            user_choice = messagebox.askyesno(
                "Conversion Issues",
                f"{error_msg}\n\nProceed anyway? (Saves partial output)\n\n'No' will roll back changes.",
                icon=messagebox.WARNING,
                parent=self.root,
            )
            if user_choice:
                self.logger.info("User chose to proceed despite failures.")
                proceed_action = True
            else:
                self.logger.warning("User chose to roll back changes.")
                _rollback_images(stats.get("generated_image_paths", []))
                stats["rolled_back"] = True
                self.conversion_completed(stats)
                return  # Stop processing

        if proceed_action:
            self.logger.info("Performing final file operations...")
            output_written = False
            if stats.get("new_content") and stats.get("output_file_path"):
                output_written = _write_output_file(
                    stats["output_file_path"], stats["new_content"]
                )
            else:
                self.logger.error(
                    "Cannot write output: Missing new content or output path."
                )

            stats["original_moved"] = False
            stats["readme_added"] = False
            stats["move_dest_dir"] = ""
            if output_written and stats.get("move_original_requested"):
                original_path = stats["input_file_path"]
                output_md_dir = os.path.dirname(original_path)
                move_dest_dir = os.path.join(output_md_dir, MERMAID_VERSION_DIR)
                output_filename = os.path.basename(stats["output_file_path"])
                image_format = stats.get("image_format", "svg")
                moved, readme = _move_original_and_readme(
                    original_path,
                    move_dest_dir,
                    stats.get("add_readme_requested", False),
                    output_filename,
                    image_format,
                )
                stats["original_moved"] = moved
                stats["readme_added"] = readme
                stats["move_dest_dir"] = move_dest_dir if moved else ""
            elif not output_written:
                self.logger.error("Skipping move/readme: output write failed.")
            elif not stats.get("move_original_requested"):
                self.logger.info("Skipping move/readme: not requested.")
            self.conversion_completed(stats)  # Show final summary

    # (_reset_ui_state remains the same)
    def _reset_ui_state(self):
        if hasattr(self, "progress") and self.progress.winfo_exists():
            self.progress.stop()
            self.progress.pack_forget()
        if hasattr(self, "convert_button") and self.convert_button.winfo_exists():
            self.convert_button.config(state=tk.NORMAL)
        self.is_processing = False
        if hasattr(self, "status_var"):
            self.status_var.set("Ready.")

    # (conversion_completed remains the same logic, uses stats dict)
    def conversion_completed(self, stats):
        if not hasattr(self, "root") or not self.root.winfo_exists():
            self.logger.warning("GUI closed before completion callback.")
            return
        self.logger.debug("Running conversion_completed callback.")
        self._reset_ui_state()
        total = stats.get("total_diagrams", 0)
        success_count = stats.get("successful_conversions", 0)
        failed_count = stats.get("failed_conversions", 0)
        output_file = stats.get("output_file_path", "N/A")
        image_dir = stats.get("image_directory", "N/A")
        moved = stats.get("original_moved", False)
        readme = stats.get("readme_added", False)
        move_dest = stats.get("move_dest_dir", "")
        rolled_back = stats.get("rolled_back", False)
        self.logger.info("=" * 25 + " Process Finished " + "=" * 25)
        summary_log = f"""
------------------- Process Summary -------------------
Input File:           {stats.get('input_file_path', 'N/A')}
Converter Used:       {stats.get('method_used', 'N/A')}
Output File:          {output_file if not rolled_back else 'N/A (Rolled Back)'}
Image Directory:      {image_dir}
Diagrams Found:       {total}
Successfully Converted: {success_count}
Failed Conversions:   {failed_count}
Rolled Back:          {'Yes' if rolled_back else 'No'}
Original Moved:       {'Yes (' + move_dest + ')' if moved else 'No'}
README Added:         {'Yes' if readme else 'No'}
----------------------------------------------------------"""
        self.logger.info(summary_log)
        final_status = "Finished."
        msg_title = "Complete"
        msg_type = messagebox.showinfo
        if rolled_back:
            final_status = f"Failed - Rolled back ({failed_count} errors)."
            message = f"{failed_count} diagram(s) failed.\n\nChanges rolled back."
            msg_title = "Rolled Back"
            msg_type = messagebox.showwarning
        elif total == 0:
            final_status = "Finished. No diagrams found."
            message = "Processing finished.\nNo Mermaid diagrams found."
        elif failed_count > 0:
            final_status = f"Completed with {failed_count} errors."
            message = f"Processing finished, but {failed_count} diagram(s) failed (check log)."
            msg_title = "Partial Success"
            msg_type = messagebox.showwarning
        else:
            final_status = "Conversion completed successfully!"
            message = "Conversion completed successfully!"
        if not rolled_back and failed_count > 0 and moved:
            message += f"\nOriginal file moved to:\n{move_dest}"
        if not rolled_back and failed_count > 0 and readme:
            message += "\nREADME.md added."
        if not rolled_back and failed_count == 0 and moved:
            message += f"\nOriginal file moved to:\n{move_dest}"
        if not rolled_back and failed_count == 0 and readme:
            message += "\nREADME.md added."
        self.status_var.set(final_status)
        msg_type(msg_title, message, parent=self.root)


# --- Main Application Entry Point ---
def main():
    """Sets up logging and starts the Tkinter GUI application."""
    root = tk.Tk()
    try:
        app = MermaidConverterGUI(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\nApplication interrupted.")
    except Exception as main_err:
        print(f"\nFATAL ERROR: {main_err}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


# --- Script Execution Guard ---
if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"\nCRITICAL FAILURE: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
