import json
import logging
import os
import queue
import shutil  # Needed for moving files
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, scrolledtext, ttk

# --- Attempt to Import Core Logic ---
try:
    from converter import load_diagram_config, process_markdown_file

    CONVERTER_AVAILABLE = True
except ImportError:
    print("ERROR: Failed to import 'converter' module.", file=sys.stderr)

    # Define dummy functions for limited GUI functionality
    def load_diagram_config(p=None):
        print("Warning: converter missing.", file=sys.stderr)
        return {}

    def process_markdown_file(**kwargs):
        print("Error: converter missing.", file=sys.stderr)
        return {"error": "Converter module not found."}

    CONVERTER_AVAILABLE = False
except Exception as import_err:
    print(
        f"ERROR: Unexpected error importing 'converter': {import_err}", file=sys.stderr
    )
    traceback.print_exc()

    def load_diagram_config(p=None):
        return {}

    def process_markdown_file(**kwargs):
        return {"error": "Converter import failed."}

    CONVERTER_AVAILABLE = False


# --- Constants ---
DEFAULT_OUTPUT_SUFFIX = "-svg"
DEFAULT_CONFIG_FILENAME = "diagram_config.json"  # Default config filename
APP_TITLE = "Mermaid Markdown Converter"  # Application window title
MERMAID_VERSION_DIR = "mermaid_version"  # Subdirectory name

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
        # Note: Formatter should be set on this handler instance.
        # The GUI thread will use this handler's formatter.
        # Set a basic default formatter here if none is provided later.
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record):
        """
        Puts the log record into the queue.
        Also stores a reference to itself on the record so the GUI thread
        can access the correct formatter associated with this handler.
        """
        record.handler = self  # Store handler reference on the record
        self.log_queue.put(record)  # Add the record to the queue


def setup_gui_logger():
    """
    Configures the root logger specifically for the GUI.
    Removes any existing handlers and adds a QueueHandler.
    """
    # Remove all existing handlers from the root logger
    # This prevents duplicate logs if the script/app is re-initialized
    for handler in logger.handlers[:]:
        try:
            handler.close()  # Close handler resources if possible
            logger.removeHandler(handler)  # Remove handler from logger
        except Exception as e:
            # Log potential errors during handler removal (to console)
            print(f"Warning: Error removing logger handler: {e}", file=sys.stderr)
            pass  # Continue trying to remove others

    # Create and configure the QueueHandler
    queue_handler = QueueHandler(log_queue)
    # Define the format for log messages displayed in the GUI
    gui_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
        datefmt="%H:%M:%S",  # Time, Level, LoggerName, Message
    )
    queue_handler.setFormatter(gui_formatter)  # Set the formatter on the handler

    # Add the QueueHandler to the root logger
    logger.addHandler(queue_handler)
    # Set the minimum logging level for the GUI (e.g., INFO)
    # Messages below this level will be ignored by this handler setup.
    logger.setLevel(logging.INFO)
    # Initial log message (will be queued and displayed shortly after GUI starts)
    # logger.info("GUI Logger Initialized.") # Logged later in __init__


def check_dependencies():
    """
    Checks if core dependencies (converter module, mermaid-py, tkinter) seem available.
    Returns a list of strings describing missing/problematic dependencies.
    """
    # Get logger for this function
    func_logger = logging.getLogger(__name__)
    missing = []
    # Check if the converter module was loaded successfully
    if not CONVERTER_AVAILABLE:
        missing.append("converter.py - Failed to load module (check console errors).")

    # Check if mermaid-py library is installed and basically functional
    try:
        import mermaid as md
        from mermaid.graph import Graph

        # Optional: Perform a very basic instantiation test (can be slow)
        # try:
        #     test_graph = Graph("flowchart", "graph TD; A-->B;")
        #     _ = md.Mermaid(test_graph)
        # except Exception as test_err:
        #     missing.append(f"mermaid-py - Basic test failed: {test_err}")
    except ImportError:
        missing.append("mermaid-py (package: python-mermaid) - Not found.")
    except Exception as e:
        # Catch other potential errors during mermaid import/check
        missing.append(f"mermaid-py - Error during import/check: {str(e)}")

    # Check if the Tkinter library itself is available
    try:
        import tkinter
    except ImportError:
        missing.append(
            "tkinter - GUI library not found (required for this application)."
        )

    func_logger.debug(f"Dependency check results: {missing or 'OK'}")
    return missing


# --- Helper Functions for File Operations ---
# These functions encapsulate file system actions called from the GUI thread.
def _write_output_file(output_path, content):
    """Writes content to the specified output file path. Returns True on success."""
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
        # Optionally show error message box here? Or let caller handle it.
        return False


def _move_original_and_readme(
    original_path, move_dest_dir, add_readme_flag, output_file_name, image_format
):
    """Creates dest dir, moves original file, optionally adds readme. Returns (moved_ok, readme_ok)."""
    func_logger = logging.getLogger(__name__)
    original_moved = False
    readme_added = False
    try:
        # Create destination directory (e.g., 'mermaid_version')
        os.makedirs(move_dest_dir, exist_ok=True)
        func_logger.info(
            f"Ensured '{os.path.basename(move_dest_dir)}' directory exists: {move_dest_dir}"
        )

        # Construct destination path for the original file
        original_filename = os.path.basename(original_path)
        move_dest_path = os.path.join(move_dest_dir, original_filename)

        # Move the original file
        func_logger.info(
            f"Attempting to move original file '{original_path}' to '{move_dest_path}'"
        )
        shutil.move(original_path, move_dest_path)
        func_logger.info(f"Successfully moved original file to: {move_dest_path}")
        original_moved = True

        # Add README if requested and move was successful
        if add_readme_flag:
            readme_path = os.path.join(move_dest_dir, "readme.md")
            output_md_dir = os.path.dirname(
                original_path
            )  # Get original dir for context in readme
            # Define README content
            readme_content = (
                f"This folder contains the original version ('{original_filename}') of a Markdown file "
                "that included Mermaid diagrams.\n\n"
                "The file was moved here because Mermaid diagrams may not render correctly "
                "in all Markdown viewers or platforms (e.g., Bitbucket).\n\n"
                "A converted version of the file, with Mermaid diagrams rendered as images "
                f"('{image_format.upper()}'), should be located in the parent directory ('{output_md_dir}') "
                f"with the name '{output_file_name}'."
            )
            try:
                # Write the README file
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(readme_content)
                func_logger.info(f"Successfully created readme.md in {move_dest_dir}")
                readme_added = True
            except Exception as readme_err:
                # Log error if README creation fails, but don't stop the process
                func_logger.error(
                    f"Failed to create readme.md in {move_dest_dir}: {readme_err}",
                    exc_info=True,
                )
                # readme_added remains False

    except OSError as move_os_err:
        # Error creating directory
        func_logger.error(
            f"Failed to create directory '{move_dest_dir}': {move_os_err}",
            exc_info=True,
        )
    except Exception as move_err:
        # Error during the actual move operation
        func_logger.error(
            f"Failed to move original file '{original_path}' to '{move_dest_dir}': {move_err}",
            exc_info=True,
        )
        original_moved = False  # Ensure flag is false if move failed

    return original_moved, readme_added


def _rollback_images(image_paths):
    """Deletes the list of generated image files during rollback. Returns count deleted."""
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
                # Log if file expected but not found
                func_logger.warning(
                    f"Image file not found during rollback (already deleted?): {img_path}"
                )
        except OSError as del_err:
            # Log error if deletion fails
            func_logger.error(
                f"Failed to delete image during rollback {img_path}: {del_err}",
                exc_info=True,
            )
    func_logger.warning(
        f"Rollback complete. Deleted {deleted_count}/{len(image_paths)} generated images."
    )
    return deleted_count


# --- Main GUI Application Class ---
class MermaidConverterGUI:
    """Encapsulates the Tkinter GUI application."""

    def __init__(self, root_window):
        """Initialize the GUI application window and widgets."""
        self.root = root_window
        self.root.title(APP_TITLE)
        self.root.geometry("850x700")
        self.root.minsize(700, 550)

        # Setup logger first
        setup_gui_logger()
        self.logger = logging.getLogger(__name__)  # Get logger for the class instance
        self.logger.info("Initializing GUI...")

        # --- Theming ---
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
            self.logger.warning(f"Could not list or set themes: {e}. Using default.")

        # --- Configure Widget Styles ---
        font_family = "Segoe UI"
        font_size = 10
        self.style.configure("TLabel", font=(font_family, font_size))
        self.style.configure("TButton", font=(font_family, font_size), padding=5)
        self.style.configure("TEntry", font=(font_family, font_size), padding=3)
        self.style.configure("TCombobox", font=(font_family, font_size))
        self.style.configure("TCheckbutton", font=(font_family, font_size))
        self.style.configure("TLabelframe.Label", font=(font_family, font_size, "bold"))
        self.style.configure("Status.TLabel", font=(font_family, font_size - 1))

        # --- Main Container Frame ---
        self.main_frame = ttk.Frame(root_window, padding="15")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame.rowconfigure(3, weight=1)  # Log frame expands
        self.main_frame.columnconfigure(0, weight=1)

        # --- Tkinter Variables (Model) ---
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

        # Set default config file path
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            default_config_path = os.path.join(script_dir, DEFAULT_CONFIG_FILENAME)
            self.config_file_var.set(default_config_path)
            self.logger.debug(f"Default config path set to: {default_config_path}")
        except NameError:
            self.config_file_var.set(DEFAULT_CONFIG_FILENAME)
            self.logger.debug(
                f"Could not determine script dir, default config path: {DEFAULT_CONFIG_FILENAME}"
            )

        # --- Create UI Sections (View) ---
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
        # **** TYPO FIX HERE ****
        self.check_and_log_dependencies()  # Corrected spelling
        self.process_log_queue()  # Start log queue monitor
        self._on_move_original_toggle()  # Set initial state of readme checkbox
        self.logger.info("GUI Initialized and Ready.")

    # --- UI Creation Methods (mostly unchanged, ellipsis for brevity) ---
    def create_file_selection_frame(self):
        # ... (same as previous version) ...
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
        # ... (same structure, ensure widgets are correctly placed) ...
        frame = ttk.LabelFrame(self.main_frame, text="Options", padding="10")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        current_row = 0
        # Row 0: Prefix, Format
        ttk.Label(frame, text="Image Prefix:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        ttk.Entry(frame, textvariable=self.image_prefix_var, width=20).grid(
            row=current_row, column=1, padx=5, pady=5, sticky=tk.W
        )
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
        current_row += 1
        # Row 1, 2: Image Dir
        ttk.Label(frame, text="Image Directory:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        ttk.Entry(frame, textvariable=self.image_dir_var, width=50).grid(
            row=current_row, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW
        )
        ttk.Button(frame, text="Browse...", command=self.browse_directory).grid(
            row=current_row, column=3, padx=5, pady=5, sticky=tk.W
        )
        current_row += 1
        ttk.Label(frame, text="(Optional. Default: 'images/' subdir)").grid(
            row=current_row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=(0, 5)
        )
        current_row += 1
        # Row 3, 4: Config File
        ttk.Label(frame, text="Config JSON:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        ttk.Entry(frame, textvariable=self.config_file_var, width=50).grid(
            row=current_row, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW
        )
        ttk.Button(frame, text="Browse...", command=self.browse_config).grid(
            row=current_row, column=3, padx=5, pady=5, sticky=tk.W
        )
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
        # Row 5: Output Suffix
        ttk.Label(frame, text="Output Suffix:").grid(
            row=current_row, column=0, sticky=tk.W, padx=5, pady=5
        )
        suffix_entry = ttk.Entry(
            frame, textvariable=self.output_suffix_var, width=15, state="readonly"
        )
        suffix_entry.grid(row=current_row, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(frame, text="(Auto-set by Format)").grid(
            row=current_row, column=2, columnspan=2, sticky=tk.W, padx=5, pady=5
        )
        current_row += 1
        # Row 6: Separator
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=current_row, column=0, columnspan=4, sticky="ew", pady=10
        )
        current_row += 1
        # Row 7: Markdown Style Checkbox
        ttk.Checkbutton(
            frame,
            text="Use standard Markdown image syntax (![alt](path))",
            variable=self.use_markdown_style_var,
        ).grid(row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=5)
        current_row += 1
        # Row 8: Move Original Checkbox
        move_cb = ttk.Checkbutton(
            frame,
            text=f"Move original file to '{MERMAID_VERSION_DIR}/' folder after conversion",
            variable=self.move_original_var,
            command=self._on_move_original_toggle,
        )
        move_cb.grid(
            row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=5
        )
        current_row += 1
        # Row 9: Add README Checkbox
        self.add_readme_checkbox = ttk.Checkbutton(
            frame,
            text=f"Add 'readme.md' explanation to '{MERMAID_VERSION_DIR}/' folder (requires move)",
            variable=self.add_readme_var,
        )
        self.add_readme_checkbox.grid(
            row=current_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=(0, 5)
        )
        current_row += 1
        return frame

    def create_action_buttons_frame(self):
        # ... (same as previous version) ...
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

    def create_log_frame(self):
        # ... (same as previous version) ...
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

    def create_status_bar(self):
        # ... (same as previous version) ...
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
    def check_and_log_dependencies(self):  # Corrected spelling
        """Checks dependencies and logs/shows messages in the GUI."""
        missing_deps = check_dependencies()
        if missing_deps:
            self.logger.error("--- Dependency Issues Detected ---")
            for dep in missing_deps:
                self.logger.error(f"  - {dep}")
            self.logger.error("Functionality may be limited or fail.")
            if "tkinter" not in str(missing_deps):
                messagebox.showwarning(
                    "Dependency Warning",
                    "Potential dependency issues:\n\n- " + "\n- ".join(missing_deps),
                    parent=self.root,
                )
        else:
            self.logger.info("Dependency check passed.")

    def _update_output_suffix(self, event=None):
        # ... (same as previous version) ...
        selected_format = self.image_format_var.get()
        new_suffix = f"-{selected_format}"
        self.output_suffix_var.set(new_suffix)
        self.logger.debug(f"Output suffix automatically updated to: {new_suffix}")

    def _on_move_original_toggle(self):
        # ... (same as previous version) ...
        if self.move_original_var.get():
            self.add_readme_checkbox.config(state=tk.NORMAL)
        else:
            self.add_readme_checkbox.config(state=tk.DISABLED)
            self.add_readme_var.set(False)
        self.logger.debug(
            f"Move original toggled: {self.move_original_var.get()}, Readme state: {self.add_readme_checkbox['state']}"
        )

    def _quit_application(self):
        # ... (same as previous version) ...
        self.logger.info("Quit button clicked. Exiting application.")
        self.root.destroy()

    def browse_file(self):
        # ... (same as previous version) ...
        file_path = filedialog.askopenfilename(
            title="Select Markdown File",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
            parent=self.root,
        )
        if file_path:
            self.file_path_var.set(file_path)
            self.logger.info(f"Input file selected: {file_path}")

    def browse_directory(self):
        # ... (same as previous version) ...
        dir_path = filedialog.askdirectory(
            title="Select Custom Image Directory (Optional)", parent=self.root
        )
        if dir_path:
            self.image_dir_var.set(dir_path)
            self.logger.info(f"Custom image directory selected: {dir_path}")

    def browse_config(self):
        # ... (same as previous version) ...
        file_path = filedialog.askopenfilename(
            title="Select Configuration JSON File (Optional)",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            parent=self.root,
        )
        if file_path:
            self.config_file_var.set(file_path)
            self.logger.info(f"Configuration file selected: {file_path}")

    def create_default_config_file(self):
        # ... (same as previous version) ...
        default_config = {
            "default": {"max_width": "600px"},
            "flowchart": {"max_width": "650px"},
        }  # Example
        file_path = filedialog.asksaveasfilename(
            title="Save Default Config As...",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=DEFAULT_CONFIG_FILENAME,
            parent=self.root,
        )
        if file_path:
            try:
                abs_path = os.path.abspath(file_path)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, indent=2)
                self.config_file_var.set(abs_path)
                self.logger.info(f"Created default config file: {abs_path}")
                messagebox.showinfo(
                    "Success",
                    f"Default configuration saved to:\n{abs_path}",
                    parent=self.root,
                )
            except Exception as e:
                self.logger.error(f"Error creating config file: {e}", exc_info=True)
                messagebox.showerror(
                    "Error", f"Could not save config file:\n{e}", parent=self.root
                )

    def edit_config_file(self):
        # ... (same as previous version) ...
        config_path = self.config_file_var.get()
        if not config_path:
            messagebox.showwarning(
                "Edit Config",
                "Please select or create a config file first.",
                parent=self.root,
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
            self.logger.info(
                f"Attempting to open '{abs_config_path}' in default editor..."
            )
            if sys.platform.startswith("win"):
                os.startfile(abs_config_path)
            elif sys.platform.startswith("darwin"):
                os.system(f'open "{abs_config_path}"')
            else:
                os.system(f'xdg-open "{abs_config_path}"')
        except Exception as e:
            self.logger.error(
                f"Error opening config file '{abs_config_path}': {e}", exc_info=True
            )
            messagebox.showerror(
                "Error", f"Could not open config file:\n{e}", parent=self.root
            )

    def log_message_to_gui(self, message, level=logging.INFO):
        # ... (same as previous version) ...
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
        # ... (same as previous version) ...
        try:
            while True:
                record = log_queue.get_nowait()
                if hasattr(record, "handler") and hasattr(record.handler, "formatter"):
                    formatted_msg = record.handler.formatter.format(record)
                else:
                    formatted_msg = f"{record.levelname}: {record.getMessage()}"
                if hasattr(self, "root") and self.root.winfo_exists():
                    self.root.after(
                        0, self.log_message_to_gui, formatted_msg, record.levelno
                    )
                if hasattr(record, "handler"):
                    del record.handler  # Avoid potential ref cycle
        except queue.Empty:
            pass
        except Exception as e:
            print(f"Error processing log queue: {e}", file=sys.stderr)
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.after(100, self.process_log_queue)

    def start_conversion(self):
        """Validates inputs and starts the conversion in a background thread."""
        if self.is_processing:
            self.logger.warning("Conversion already in progress.")
            messagebox.showwarning(
                "Busy", "Conversion already running.", parent=self.root
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
        if not CONVERTER_AVAILABLE or process_markdown_file is None:
            messagebox.showerror(
                "Critical Error", "Core converter module not loaded.", parent=self.root
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

        # --- Gather Options ---
        options = {
            "image_prefix": self.image_prefix_var.get() or "diagram",
            "image_format": self.image_format_var.get(),
            "image_dir": self.image_dir_var.get() or None,
            "config_path_input": self.config_file_var.get() or None,
            "use_html_wrapper": not self.use_markdown_style_var.get(),
            "output_suffix": self.output_suffix_var.get(),
            # Store move/readme options separately for use after thread returns
            "move_original_requested": self.move_original_var.get(),
            "add_readme_requested": self.add_readme_var.get(),
        }

        # --- Load Config (Main Thread) ---
        try:
            options["diagram_config"] = load_diagram_config(
                options["config_path_input"]
            )
            self.logger.info(
                f"Loaded diagram config (source: {options['config_path_input'] or 'default'})"
            )
        except Exception as config_err:
            self.logger.error(
                f"Failed to load diagram config: {config_err}", exc_info=True
            )
            messagebox.showerror(
                "Config Error",
                f"Error loading config:\n{config_err}\n\nUsing defaults.",
                parent=self.root,
            )
            options["diagram_config"] = {}

        # --- Resolve Image Dir (Main Thread) ---
        if options["image_dir"]:
            try:
                options["image_dir"] = os.path.abspath(options["image_dir"])
            except Exception as path_err:
                self.logger.error(
                    f"Invalid image dir path '{options['image_dir']}': {path_err}",
                    exc_info=True,
                )
                messagebox.showerror(
                    "Path Error",
                    f"Invalid image directory:\n{options['image_dir']}",
                    parent=self.root,
                )
                self._reset_ui_state()
                return  # Reset UI and stop

        # --- Start Background Thread ---
        self.logger.info("Starting conversion in background thread...")
        conversion_thread = threading.Thread(
            target=self.run_conversion_thread,
            args=(abs_file_path, options),  # Pass path and options dict
            daemon=True,
        )
        conversion_thread.start()

    def run_conversion_thread(self, abs_file_path, options):
        """Runs process_markdown_file in background. Schedules GUI callback."""
        self.logger.debug(
            f"Background thread started for {os.path.basename(abs_file_path)}"
        )
        stats = {}  # Initialize stats
        try:
            if process_markdown_file is None:
                raise RuntimeError("process_markdown_file missing.")

            # Call the core processing function (no move/readme args needed now)
            stats = process_markdown_file(
                file_path=abs_file_path,
                image_prefix=options["image_prefix"],
                image_format=options["image_format"],
                image_dir=options["image_dir"],
                diagram_config=options["diagram_config"],
                use_html_wrapper=options["use_html_wrapper"],
                output_suffix=options["output_suffix"],
            )
            # Add requested move/readme flags back into stats for the GUI thread
            stats["move_original_requested"] = options["move_original_requested"]
            stats["add_readme_requested"] = options["add_readme_requested"]
            # Store image format for potential use in readme helper
            stats["image_format"] = options["image_format"]

            self.logger.debug(
                "process_markdown_file completed. Scheduling GUI result handler."
            )
            # Schedule the NEW result handler in the main thread
            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after(0, self.handle_conversion_result, stats)

        except Exception as e:
            thread_error_msg = f"Error during conversion process: {str(e)}"
            self.logger.critical(thread_error_msg, exc_info=True)
            # Include error in stats if possible
            stats["error"] = thread_error_msg
            stats["all_conversions_successful"] = False
            # Schedule the failure handler (or the result handler which checks for error)
            if hasattr(self, "root") and self.root.winfo_exists():
                # Ensure stats dict is passed even on error
                self.root.after(0, self.handle_conversion_result, stats)

    def handle_conversion_result(self, stats):
        """
        Handles the result from the conversion thread. Runs in GUI thread.
        Decides whether to proceed, rollback, or prompt user based on success/failure.
        """
        self.logger.debug("Running handle_conversion_result callback in GUI thread.")

        # Check for early critical errors from converter
        if stats.get("error"):
            self.logger.error(f"Conversion failed early: {stats['error']}")
            self._reset_ui_state()  # Reset UI
            messagebox.showerror(
                "Conversion Failed",
                f"Could not process file:\n{stats['error']}",
                parent=self.root,
            )
            return

        all_successful = stats.get("all_conversions_successful", False)
        proceed_action = False  # Flag to determine final action

        if all_successful:
            self.logger.info(
                "All diagrams converted successfully. Proceeding with file operations."
            )
            proceed_action = True
        else:
            # --- Conversion Failed - Prompt User ---
            failed_count = stats.get("failed_conversions", "Some")
            self.logger.warning(
                f"{failed_count} diagram(s) failed conversion. Prompting user."
            )
            # Use askyesno: returns True for Yes, False for No
            user_choice = messagebox.askyesno(
                "Conversion Failed",
                f"{failed_count} diagram(s) failed to convert.\n\n"
                "Proceed anyway? (Saves partial output, moves original if requested)\n\n"
                "Selecting 'No' will roll back changes (delete generated images).",
                icon=messagebox.WARNING,  # Use warning icon
                parent=self.root,
            )

            if user_choice:  # User selected Yes (Proceed)
                self.logger.info("User chose to proceed despite failures.")
                proceed_action = True
            else:  # User selected No (Rollback)
                self.logger.warning("User chose to roll back changes.")
                # Call rollback helper function
                _rollback_images(stats.get("generated_image_paths", []))
                # Update stats to indicate rollback for the final summary
                stats["rolled_back"] = True
                # Call completion summary directly after rollback
                self.conversion_completed(stats)  # Shows summary, resets UI
                return  # Stop further processing

        # --- Perform Final Actions (if Proceeding) ---
        if proceed_action:
            self.logger.info("Performing final file operations...")
            # Write the output markdown file
            output_written = _write_output_file(
                stats["output_file_path"], stats["new_content"]
            )

            # Initialize move/readme results in stats
            stats["original_moved"] = False
            stats["readme_added"] = False
            stats["move_dest_dir"] = ""

            # Perform move/readme only if output was written and move was requested
            if output_written and stats.get("move_original_requested"):
                original_path = stats["input_file_path"]
                output_md_dir = os.path.dirname(original_path)
                move_dest_dir = os.path.join(output_md_dir, MERMAID_VERSION_DIR)
                output_filename = os.path.basename(stats["output_file_path"])
                image_format = stats.get("image_format", "svg")  # Get format for readme

                # Call move/readme helper function
                moved, readme = _move_original_and_readme(
                    original_path,
                    move_dest_dir,
                    stats.get("add_readme_requested", False),
                    output_filename,
                    image_format,
                )
                # Update stats with results from helper
                stats["original_moved"] = moved
                stats["readme_added"] = readme
                stats["move_dest_dir"] = move_dest_dir if moved else ""
            elif not output_written:
                self.logger.error(
                    "Skipping move/readme because output file failed to write."
                )
                # Show error to user?
                messagebox.showerror(
                    "File Error",
                    f"Failed to write output file:\n{stats['output_file_path']}\nOriginal file not moved.",
                    parent=self.root,
                )
            elif not stats.get("move_original_requested"):
                self.logger.info("Skipping move/readme because it was not requested.")

            # Call the final summary display function
            self.conversion_completed(stats)

    def _reset_ui_state(self):
        """Helper to reset progress bar and convert button state."""
        # Check if widgets exist before configuring (might be called during shutdown)
        if hasattr(self, "progress") and self.progress.winfo_exists():
            self.progress.pack_forget()
            self.progress.stop()
        if hasattr(self, "convert_button") and self.convert_button.winfo_exists():
            self.convert_button.config(state=tk.NORMAL)
        self.is_processing = False
        # Check if status_var exists before setting
        if hasattr(self, "status_var"):
            self.status_var.set("Ready.")  # Reset status bar

    def conversion_completed(self, stats):
        """Updates GUI after conversion process finishes (success, partial, or rollback)."""
        if not hasattr(self, "root") or not self.root.winfo_exists():
            self.logger.warning(
                "GUI window closed before conversion completion callback."
            )
            return

        self.logger.debug("Running conversion_completed callback.")
        # Reset UI state (progress bar, button)
        self._reset_ui_state()  # Use helper function

        # Extract stats (handle potential missing keys gracefully)
        total = stats.get("total_diagrams", 0)
        success = stats.get("successful_conversions", 0)
        failed = stats.get("failed_conversions", 0)
        output_file = stats.get("output_file_path", "N/A")
        image_dir = stats.get("image_directory", "N/A")
        moved = stats.get("original_moved", False)
        readme = stats.get("readme_added", False)
        move_dest = stats.get("move_dest_dir", "")
        rolled_back = stats.get("rolled_back", False)  # Check if rollback occurred

        self.logger.info("=" * 25 + " Process Finished " + "=" * 25)
        # Log detailed summary
        summary_log = f"""
------------------- Process Summary -------------------
Input File:           {stats.get('input_file_path', 'N/A')}
Output File:          {output_file if not rolled_back else 'N/A (Rolled Back)'}
Image Directory:      {image_dir}
Diagrams Found:       {total}
Successfully Converted: {success}
Failed Conversions:   {failed}
Rolled Back:          {'Yes' if rolled_back else 'No'}
Original Moved:       {'Yes (' + move_dest + ')' if moved else 'No'}
README Added:         {'Yes' if readme else 'No'}
----------------------------------------------------------
"""
        self.logger.info(summary_log)  # Logged to file/console via queue handler

        # Determine final status message and show appropriate messagebox
        final_status = "Finished."
        msg_title = "Complete"
        msg_type = messagebox.showinfo

        if rolled_back:
            final_status = f"Failed - Rolled back ({failed} errors)."
            message = f"{failed} diagram(s) failed to convert.\n\nChanges were rolled back (generated images deleted, no output file created)."
            msg_title = "Rolled Back"
            msg_type = messagebox.showwarning
        elif total == 0:
            final_status = "Finished. No diagrams found."
            message = "Processing finished.\nNo Mermaid diagrams were found."
        elif failed > 0:
            # This case now implies user chose 'Proceed' despite failures
            final_status = f"Completed with {failed} errors (User Proceeded)."
            message = f"Processing finished, but {failed} diagram(s) failed to convert (saved in output).\nPlease check the log and output file for details."
            if moved:
                message += f"\nOriginal file moved to:\n{move_dest}"
            if readme:
                message += "\nREADME.md added."
            msg_title = "Partial Success"
            msg_type = messagebox.showwarning
        else:  # All successful
            final_status = "Conversion completed successfully!"
            message = "Conversion completed successfully!"
            if moved:
                message += f"\nOriginal file moved to:\n{move_dest}"
            if readme:
                message += "\nREADME.md added."

        self.status_var.set(final_status)
        # Show the summary message box to the user
        msg_type(msg_title, message, parent=self.root)


# --- Main Application Entry Point ---
def main():
    """Sets up logging and starts the Tkinter GUI application."""
    root = tk.Tk()
    try:
        app = MermaidConverterGUI(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\nApplication interrupted by user (Ctrl+C).")
        if "app" in locals() and hasattr(app, "root") and app.root.winfo_exists():
            app.root.destroy()
    except Exception as main_err:
        print(f"\nFATAL ERROR: {main_err}", file=sys.stderr)
        traceback.print_exc()
        try:
            if root and root.winfo_exists():
                messagebox.showerror(
                    "Fatal Error",
                    f"Failed to run application:\n\n{main_err}",
                    parent=root,
                )
        except Exception:
            pass
        sys.exit(1)


# --- Script Execution Guard ---
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nCRITICAL FAILURE: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
