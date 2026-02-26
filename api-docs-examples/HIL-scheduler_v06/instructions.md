# How to Install Project Dependencies

This project uses Python and requires several external packages to run. These dependencies are listed in the `requirements.txt` file.

To ensure a clean and isolated environment for the project, it is highly recommended to use a Python virtual environment. This prevents conflicts with other projects or your system's global Python installation.

## Installation Steps

1.  **Open a terminal or command prompt** (like PowerShell or Command Prompt on Windows).

2.  **Navigate to the project directory:**
    ```powershell
    cd "c:\Users\jalon\Documents\uc3m\Proyectos\2023 i-STENTORE\HIL-scheduler"
    ```

3.  **Create a virtual environment.** This command creates a new folder named `venv` which will contain the Python interpreter and all the project-specific packages.
    ```powershell
    python -m venv venv
    ```

4.  **Activate the virtual environment.** You must activate the environment in every new terminal session you open to work on the project.

    *   **On Windows (PowerShell/CMD):**
        ```powershell
        .\venv\Scripts\activate
        ```

    *   **On macOS/Linux (bash/zsh):**
        ```bash
        source venv/bin/activate
        ```

    Your terminal prompt should now be prefixed with `(venv)`, indicating that the virtual environment is active.

5.  **Install the required packages.** With the virtual environment active, use `pip` to install all packages listed in `requirements.txt` with a single command:
    ```powershell
    pip install -r requirements.txt
    ```

After these steps are complete, all necessary packages (`pandas`, `pyModbusTCP`, and `numpy`) will be installed inside the `venv` folder, and you will be ready to run the `hil_scheduler.py` script.