import os
import ibm_db_dbi as db
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, ListView, ListItem, Label, Static, Button, TextArea
from textual.containers import Vertical, Container, Center
from textual.binding import Binding
from textual.screen import ModalScreen
from textual import on
from datetime import datetime
from pathlib import Path
from typing import Optional

# ************ VERSION 0.1 ******************

# Db2 server family (set at connect time via probe queries)
class Db2ServerKind:
    UNKNOWN = "unknown"
    IBM_I = "ibm_i"
    LUW = "luw"
    ZOS = "zos"


# --- FORCE SAFE MODE (Prevents display glitches) ---
os.environ["NCURSES_NO_UTF8_ACS"] = "1"
os.environ["TEXTUAL_DRIVER"] = "linux"
os.environ["LANG"] = "en_US.UTF-8"

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
MAX_AUTO_EXECUTE_LENGTH = 200  # Don't auto-execute SQL longer than this

# ═══════════════════════════════════════════════════════════════════
# DEBUG SETTINGS
# ═══════════════════════════════════════════════════════════════════
DEBUG_KEYBOARD_FLAG = False  # Set to True to log all keyboard events

# ═══════════════════════════════════════════════════════════════════
# COLOR THEME CONFIGURATION - Change these to customize the look
# ═══════════════════════════════════════════════════════════════════
COLOR_BACKGROUND = "#000000"        # Main background (black)
COLOR_TEXT_NORMAL = "#00ff00"       # Normal text (green)
COLOR_TEXT_SELECTED = "#ff8800"     # Selected text (orange)
COLOR_SELECTION_BG = "#000000"      # Selection background (black)
COLOR_HOVER = "#1a1a1a"             # Hover background (dark gray)
COLOR_HEADER_BG = "#003366"         # Header background (dark blue)
COLOR_HEADER_TEXT = "#ffffff"       # Header text (white)
COLOR_SIDEBAR_BG = "#000000"        # Sidebar background (black)
COLOR_BORDER = "#00ff00"            # Border color (green)
COLOR_BORDER_FOCUS = "#00ffff"      # Focused border (cyan)
COLOR_TABLE_HEADER = "#003366"      # Table header background
COLOR_STATUS_BG = "#003366"         # Status bar background
COLOR_PAGINATION_BG = "#004400"     # Pagination bar background
COLOR_ABOUT_BORDER = "#ff8800"      # About dialog border (orange)
COLOR_LISTVIEW_TEXT = "#00ff00"     # ListView/TreeView text color (green)


class DB2Client:
    def __init__(self):
        self.conn = None
        self.cursor = None
        self.last_query = ""
        self.server_kind = Db2ServerKind.UNKNOWN
        self.server_version = ""

    def _try_query(self, sql: str) -> bool:
        """Run a read-only probe; return True if it succeeds."""
        try:
            self.cursor.execute(sql)
            self.cursor.fetchall()
            return True
        except Exception:
            return False

    def _set_version_from_query(self, sql: str) -> None:
        try:
            self.cursor.execute(sql)
            row = self.cursor.fetchone()
            if row:
                self.server_version = " ".join(str(x).strip() for x in row if x is not None)
        except Exception:
            pass

    def detect_server(self) -> None:
        """
        Identify Db2 product at the connected server (not the client OS).
        Order matters: IBM i is probed before LUW because IBM i can expose SYSIBMADM views too.
        """
        self.server_kind = Db2ServerKind.UNKNOWN
        self.server_version = ""

        if self._try_query("SELECT 1 FROM QSYS2.SYSSCHEMAS FETCH FIRST 1 ROW ONLY"):
            self.server_kind = Db2ServerKind.IBM_I
            self._set_version_from_query(
                "SELECT OS_VERSION, OS_RELEASE FROM SYSIBMADM.ENV_SYS_INFO FETCH FIRST 1 ROW ONLY"
            )
            return

        if self._try_query("SELECT 1 FROM SYSIBMADM.ENV_SYS_INFO FETCH FIRST 1 ROW ONLY"):
            self.server_kind = Db2ServerKind.LUW
            self._set_version_from_query(
                "SELECT SERVICE_LEVEL, RELEASE_NUMBER FROM SYSIBMADM.ENV_INST_INFO FETCH FIRST 1 ROW ONLY"
            )
            if not self.server_version:
                self._set_version_from_query(
                    "SELECT INST_NAME, OS_NAME FROM SYSIBMADM.ENV_SYS_INFO FETCH FIRST 1 ROW ONLY"
                )
            return

        if self._try_query("SELECT 1 FROM SYSIBM.SYSSCHEMATA FETCH FIRST 1 ROW ONLY"):
            self.server_kind = Db2ServerKind.ZOS
            self._set_version_from_query(
                "SELECT VERSION FROM SYSIBM.SYSDUMMY1 FETCH FIRST 1 ROW ONLY"
            )
            return

        if self._try_query("SELECT 1 FROM SYSCAT.SCHEMATA FETCH FIRST 1 ROW ONLY"):
            self.server_kind = Db2ServerKind.LUW
            self.server_version = "LUW (SYSCAT only; SYSIBMADM not accessible)"
            return

        self.server_kind = Db2ServerKind.UNKNOWN

    def connect(self):
        try:
            self.conn = db.connect(dsn="PYTHON")
            self.cursor = self.conn.cursor()
            self.detect_server()
            label = {
                Db2ServerKind.LUW: "Db2 LUW",
                Db2ServerKind.IBM_I: "Db2 for IBM i",
                Db2ServerKind.ZOS: "Db2 for z/OS",
                Db2ServerKind.UNKNOWN: "Db2 (product unknown)",
            }.get(self.server_kind, "Db2")
            ver = f" — {self.server_version}" if self.server_version else ""
            return True, f"Connected ({label}{ver})"
        except Exception as e:
            return False, str(e)
    
    def commit(self):
        """Commit the current transaction - CRITICAL for DDL persistence"""
        try:
            if self.conn:
                self.conn.commit()
                return True, "Transaction committed"
            return False, "No connection"
        except Exception as e:
            return False, str(e)
    
    def get_schemas(self):
        kind = self.server_kind
        queries = []
        if kind == Db2ServerKind.IBM_I:
            queries.append("SELECT SCHEMA_NAME FROM QSYS2.SYSSCHEMAS ORDER BY SCHEMA_NAME")
        elif kind == Db2ServerKind.LUW:
            queries.append("SELECT SCHEMANAME FROM SYSCAT.SCHEMATA ORDER BY SCHEMANAME")
            queries.append("SELECT NAME FROM SYSIBM.SYSSCHEMATA ORDER BY NAME")
        elif kind == Db2ServerKind.ZOS:
            queries.append("SELECT NAME FROM SYSIBM.SYSSCHEMATA ORDER BY NAME")
            queries.append(
                "SELECT DISTINCT CREATOR FROM SYSIBM.SYSTABLES ORDER BY CREATOR"
            )
        else:
            queries.extend(
                [
                    "SELECT SCHEMANAME FROM SYSCAT.SCHEMATA ORDER BY SCHEMANAME",
                    "SELECT NAME FROM SYSIBM.SYSSCHEMATA ORDER BY NAME",
                    "SELECT SCHEMA_NAME FROM QSYS2.SYSSCHEMAS ORDER BY SCHEMA_NAME",
                ]
            )
        for sql in queries:
            try:
                self.cursor.execute(sql)
                return [r[0] for r in self.cursor.fetchall()]
            except Exception:
                continue
        return []

    def get_tables(self, schema):
        sch = schema.upper()
        kind = self.server_kind
        pairs = []
        if kind == Db2ServerKind.IBM_I:
            pairs.append(
                (
                    "SELECT TABLE_NAME FROM QSYS2.SYSTABLES "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE IN ('T', 'P') ORDER BY TABLE_NAME",
                    (sch,),
                )
            )
        elif kind == Db2ServerKind.LUW:
            pairs.append(
                (
                    "SELECT TABNAME FROM SYSCAT.TABLES "
                    "WHERE TABSCHEMA = ? AND TYPE = 'T' ORDER BY TABNAME",
                    (sch,),
                )
            )
            pairs.append(
                (
                    "SELECT NAME FROM SYSIBM.SYSTABLES WHERE CREATOR = ? ORDER BY NAME",
                    (sch,),
                )
            )
        elif kind == Db2ServerKind.ZOS:
            pairs.append(
                (
                    "SELECT NAME FROM SYSIBM.SYSTABLES WHERE CREATOR = ? ORDER BY NAME",
                    (sch,),
                )
            )
        else:
            pairs.extend(
                [
                    (
                        "SELECT TABNAME FROM SYSCAT.TABLES "
                        "WHERE TABSCHEMA = ? AND TYPE = 'T' ORDER BY TABNAME",
                        (sch,),
                    ),
                    (
                        "SELECT NAME FROM SYSIBM.SYSTABLES WHERE CREATOR = ? ORDER BY NAME",
                        (sch,),
                    ),
                    (
                        "SELECT TABLE_NAME FROM QSYS2.SYSTABLES "
                        "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE IN ('T', 'P') ORDER BY TABLE_NAME",
                        (sch,),
                    ),
                ]
            )
        for sql, params in pairs:
            try:
                self.cursor.execute(sql, params)
                return [r[0] for r in self.cursor.fetchall()]
            except Exception:
                continue
        return []
    
    def get_table_count(self, schema, table):
        """Get total row count for a table"""
        try:
            sql = f"SELECT COUNT(*) FROM {schema}.{table}"
            self.cursor.execute(sql)
            return self.cursor.fetchone()[0]
        except:
            return 0
    
    def run_query_paginated(self, sql, offset=0, limit=50):
        """Run query with pagination support"""
        try:
            sql_upper = sql.upper().strip()
            if "LIMIT" not in sql_upper and "FETCH FIRST" not in sql_upper:
                paginated_sql = f"{sql} FETCH FIRST {limit} ROWS ONLY"
            else:
                paginated_sql = sql
            
            if offset > 0 and "OFFSET" not in sql_upper:
                paginated_sql = f"""
                SELECT * FROM (
                    SELECT ROW_NUMBER() OVER() AS RN, T.* 
                    FROM ({sql}) T
                ) WHERE RN > {offset} FETCH FIRST {limit} ROWS ONLY
                """
            
            self.last_query = paginated_sql
            self.cursor.execute(paginated_sql)
            
            if self.cursor.description:
                headers = [d[0] for d in self.cursor.description]
                if headers and headers[0] == 'RN':
                    headers = headers[1:]
                    rows = [row[1:] for row in self.cursor.fetchall()]
                else:
                    rows = self.cursor.fetchall()
                return headers, rows, None
            
            return [], [], "Query executed successfully (no results)"
        except Exception as e:
            return [], [], str(e)
    
    def run_query(self, sql):
        """Simple query execution without pagination"""
        try:
            self.cursor.execute(sql)
            if self.cursor.description:
                return [d[0] for d in self.cursor.description], self.cursor.fetchall(), None
            return [], [], "Success"
        except Exception as e:
            return [], [], str(e)
    
    def execute_batch(self, sql_script):
        """Execute multiple SQL statements separated by semicolons"""
        results = []
        try:
            statements = self._split_sql_statements(sql_script)
            
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt or not self._is_executable(stmt):
                    continue
                
                self.cursor.execute(stmt)
                
                if self._needs_commit(stmt):
                    self.conn.commit()
                
                if self.cursor.description:
                    headers = [d[0] for d in self.cursor.description]
                    rows = self.cursor.fetchall()
                    results.append((stmt, headers, rows, None))
                else:
                    results.append((stmt, [], [], "Success"))
            
            return results
        except Exception as e:
            if self.conn:
                self.conn.rollback()
            return [(sql_script, [], [], f"Batch Error: {e}")]
    
    
    def _split_sql_statements(self, sql_script: str) -> list[str]:
        """Split on semicolon, drop empties, strip whitespace."""
        return [s.strip() for s in sql_script.split(";") if s.strip()]    
    
    
    def _is_executable(self, stmt):
        """Check if statement is not just comments or whitespace"""
        cleaned = ' '.join(stmt.strip().split())
        return cleaned and not cleaned.startswith('--')
    
    def _needs_commit(self, stmt):
        """Check if statement is DDL/DML that requires commit"""
        keywords = ['CREATE', 'DROP', 'ALTER', 'INSERT', 'UPDATE', 'DELETE']
        first_word = stmt.strip().split()[0].upper()
        return first_word in keywords


class SchemaItem(ListItem):
    def __init__(self, name: str):
        self.schema_name = name
        super().__init__(Label(name))


class TableItem(ListItem):
    def __init__(self, name: str, row_count: int = None):
        self.table_name = name
        self.row_count = row_count
        display = f"{name} ({row_count} rows)" if row_count is not None else name
        super().__init__(Label(display))


class StatusBar(Static):
    """Custom status bar to show query info"""
    def __init__(self):
        super().__init__("")
        self.update_status("Ready")
    
    def update_status(self, message: str, style: str = "info"):
        styles = {
            "info": "INFO",
            "success": "OK",
            "error": "ERR",
            "query": "RUN"
        }
        icon = styles.get(style, "INFO")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.update(f"[{icon}] [{timestamp}] {message}")


class MessagePanel(Static):
    """Message output panel for logs and errors"""
    def __init__(self):
        super().__init__("")
        self.messages = []
        self.max_messages = 100
    
    def add_message(self, message: str, msg_type: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {
            "info": "[i]",
            "success": "[+]",
            "error": "[X]",
            "warning": "[!]",
            "query": "[>]"
        }
        icon = icons.get(msg_type, "[i]")
        
        formatted_msg = f"[{timestamp}] {icon} {message}"
        self.messages.append(formatted_msg)
        
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        
        display_messages = self.messages[-5:]
        self.update("\n".join(display_messages))
    
    def clear_messages(self):
        self.messages = []
        self.update("")


class PaginationBar(Static):
    """Pagination control bar"""
    def __init__(self):
        super().__init__("")
        self.current_page = 1
        self.total_pages = 1
        self.page_size = 50
        self.total_rows = 0
        self.update_display()
    
    def update_display(self):
        start = (self.current_page - 1) * self.page_size + 1
        end = min(self.current_page * self.page_size, self.total_rows)
        self.update(
            f"Page {self.current_page}/{self.total_pages} | "
            f"Rows {start}-{end} of {self.total_rows} | "
            f"Size: {self.page_size} | "
            f"(n)ext (p)rev (f)irst (l)ast (s)ize"
        )


class AboutScreen(ModalScreen):
    """About dialog screen"""
    
    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="about-dialog"):
                yield Label("IBM DB2i Simple SQL IDE", id="about-title")
                with Vertical(id="about-content"):
                    yield Label("")
                    yield Label("Implemented by:", classes="about-info")
                    yield Label("John Tsioumpris", classes="about-info")
                    yield Label("")
                    yield Label("solutions4it.guru", classes="about-info")
                    yield Label("tsgiannis@gmail.com", classes="about-info")
                    yield Label("")
                with Center():
                    yield Button("Close", id="about-button", variant="success")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.app.pop_screen()
    
    def on_key(self, event) -> None:
        if event.key == "escape" or event.key == "enter":
            self.app.pop_screen()


class LoadFileScreen(ModalScreen):
    """File loader dialog"""

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=False, priority=True),
    ]

    def action_dismiss_cancel(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="file-dialog"):
                yield Label("Load SQL File", id="file-title")
                yield Input(placeholder="Enter file path (e.g., /home/user/query.sql)", id="file-path")
                with Center():
                    yield Button("Load", id="load-button", variant="primary")
                    yield Button("Cancel", id="cancel-button", variant="error")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load-button":
            file_path = self.query_one("#file-path").value.strip()
            if file_path:
                self.dismiss(file_path)
            else:
                self.dismiss()
        else:
            self.dismiss()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        file_path = event.value.strip()
        if file_path:
            self.dismiss(file_path)


class SqlApp(App):
    TITLE = "DB2 TUI Client - Enhanced Edition"
    
    debug_keyboard_flag = DEBUG_KEYBOARD_FLAG
    
    CSS = f"""
    Screen {{ 
        layout: vertical; 
        background: {COLOR_BACKGROUND};
    }}
    
    #top-bar {{ 
        dock: top; 
        height: 3; 
        background: {COLOR_HEADER_BG}; 
        color: {COLOR_HEADER_TEXT};
        padding: 0 1;
    }}
    
    #sidebar {{ 
        dock: left; 
        width: 35; 
        background: {COLOR_SIDEBAR_BG};
        color: {COLOR_TEXT_NORMAL};
        border-right: solid {COLOR_BORDER};
    }}
    
    #right-panel {{
        layout: vertical;
        width: 1fr;
        background: {COLOR_BACKGROUND};
    }}
    
    #query-panel {{
        height: auto;
        min-height: 5;
        max-height: 20;
        background: {COLOR_BACKGROUND};
    }}

    TextArea {{
        border: solid {COLOR_BORDER};
        background: {COLOR_BACKGROUND};
        color: {COLOR_TEXT_NORMAL};
        margin: 1;
        height: auto;
        min-height: 5;
    }}
    
    TextArea:focus {{
        border: solid {COLOR_BORDER_FOCUS};
    }}
    
    #sql-hint {{
        background: {COLOR_PAGINATION_BG};
        color: {COLOR_TEXT_SELECTED};
        padding: 0 1;
        text-align: center;
        text-style: bold;
    }}

    Input {{ 
        border: solid {COLOR_BORDER}; 
        background: {COLOR_BACKGROUND}; 
        color: {COLOR_TEXT_NORMAL};
        margin: 1;
    }}
    
    Input:focus {{
        border: solid {COLOR_BORDER_FOCUS};
    }}
    
    ListView {{
        background: {COLOR_SIDEBAR_BG};
        color: {COLOR_LISTVIEW_TEXT};
    }}
    
    ListView > Label {{
        color: {COLOR_LISTVIEW_TEXT};
    }}
    
    ListItem {{
        background: {COLOR_SELECTION_BG};
        color: {COLOR_LISTVIEW_TEXT};
        padding: 0 1;
    }}
    
    ListItem > Label {{
        color: {COLOR_LISTVIEW_TEXT};
    }}
    
    ListItem.--highlight {{
        background: {COLOR_SELECTION_BG};
        color: {COLOR_TEXT_SELECTED}; 
        text-style: bold;
    }}
    
    ListItem.--highlight > Label {{
        color: {COLOR_TEXT_SELECTED};
    }}
    
    ListItem:hover {{
        background: {COLOR_HOVER};
        color: {COLOR_TEXT_SELECTED};
    }}
    
    ListItem:hover > Label {{
        color: {COLOR_TEXT_SELECTED};
    }}

    DataTable {{
        background: {COLOR_BACKGROUND};
        color: {COLOR_TEXT_NORMAL};
        height: 1fr;
    }}
    
    DataTable > .datatable--header {{
        background: {COLOR_TABLE_HEADER};
        color: {COLOR_HEADER_TEXT};
        text-style: bold;
    }}
    
    DataTable > .datatable--cursor {{
        background: {COLOR_SELECTION_BG};
        color: {COLOR_TEXT_SELECTED};
        text-style: bold;
    }}
    
    StatusBar {{
        dock: bottom;
        height: 1;
        background: {COLOR_STATUS_BG};
        color: {COLOR_HEADER_TEXT};
        padding: 0 1;
    }}
    
    MessagePanel {{
        dock: bottom;
        height: 6;
        background: {COLOR_BACKGROUND};
        color: {COLOR_TEXT_NORMAL};
        border-top: solid {COLOR_BORDER};
        padding: 0 1;
        overflow-y: auto;
    }}
    
    PaginationBar {{
        dock: bottom;
        height: 1;
        background: {COLOR_PAGINATION_BG};
        color: {COLOR_TEXT_NORMAL};
        padding: 0 1;
    }}
    
    .header-text {{
        background: {COLOR_HEADER_BG};
        color: {COLOR_HEADER_TEXT};
        text-align: center;
        text-style: bold;
        padding: 0 1;
    }}
    
    AboutScreen {{
        align: center middle;
    }}
    
    #about-dialog {{
        width: 60;
        height: 26;
        background: {COLOR_HEADER_BG};
        border: thick {COLOR_ABOUT_BORDER};
        padding: 1 2;
    }}
    
    #about-content {{
        width: 100%;
        height: auto;
        color: {COLOR_HEADER_TEXT};
        text-align: center;
    }}
    
    #about-title {{
        text-style: bold;
        color: {COLOR_ABOUT_BORDER};
        text-align: center;
        padding: 1 0;
    }}
    
    .about-info {{
        color: {COLOR_TEXT_NORMAL};
        text-align: center;
        padding: 1 0;
    }}
    
    #about-button {{
        width: 20;
        margin: 1 0;
    }}
    
    LoadFileScreen {{
        align: center middle;
    }}
    
    #file-dialog {{
        width: 70;
        height: 12;
        background: {COLOR_HEADER_BG};
        border: thick {COLOR_BORDER};
        padding: 1 2;
    }}
    
    #file-title {{
        text-style: bold;
        color: {COLOR_TEXT_SELECTED};
        text-align: center;
        padding: 1 0;
    }}
    
    #file-path {{
        width: 100%;
        margin: 1 0;
    }}
    
    #load-button, #cancel-button {{
        margin: 0 1;
    }}
    """
    
    BINDINGS = [
        Binding("n", "next_page", "Next Page", show=True),
        Binding("p", "prev_page", "Prev Page", show=True),
        Binding("f", "first_page", "First Page", show=True),
        Binding("l", "last_page", "Last Page", show=True),
        Binding("s", "change_page_size", "Change Page Size", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("ctrl+o", "load_file", "Open SQL", show=True),
        Binding("ctrl+e", "execute_sql", "Execute", show=True, priority=True),
        Binding("c", "clear_table", "Clear", show=False),
        Binding("ctrl+a", "show_about", "About", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("b", "back_to_schemas", "Schema list", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.client = DB2Client()
        self.current_schema = ""
        self.current_table = ""
        self.current_sql = ""
        self.current_offset = 0
        self.page_size = 50
        self.total_rows = 0
        self.loaded_sql = ""
        self.loaded_file_name = ""
        self.sidebar_showing_schemas = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with Container(id="top-bar"):
            yield Input(
                placeholder="Optional: type a schema name and press Enter to open its tables",
                id="schema",
            )

        with Container(id="main"):
            with Vertical(id="sidebar"):
                yield Label("SCHEMAS", id="sidebar-title", classes="header-text")
                yield ListView(id="list")
            
            with Vertical(id="right-panel"):
                with Container(id="query-panel"):
                    yield Label("Press Ctrl+E to Execute SQL | Ctrl+O to Load File", id="sql-hint")
                    yield TextArea(id="sql", language="sql")
                yield DataTable(id="results-table", cursor_type="row")
                yield PaginationBar()
        
        yield MessagePanel()
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        """Initialize app - runs once at startup"""
        status, msg = self.client.connect()
        self.add_message(msg, "success" if status else "error")
        self.query_one(StatusBar).update_status(msg, "success" if status else "error")
        
        dt = self.query_one("#results-table")
        dt.zebra_stripes = True
        dt.cursor_type = "row"
        
        if status:
            self.load_schema_list(focus_list=True)
        else:
            self.query_one("#schema").focus()

    def on_key(self, event) -> None:
        """Log key presses only when debug mode is enabled"""
        if self.debug_keyboard_flag:
            if event.key not in ["up", "down", "left", "right"]:
                self.add_message(f"[KEY] {event.key}", "info")

    def add_message(self, message: str, msg_type: str = "info"):
        """Add message to message panel"""
        self.query_one(MessagePanel).add_message(message, msg_type)

    def load_schema_list(self, focus_list: bool = False) -> None:
        """Populate sidebar with schemas from the catalog."""
        self.sidebar_showing_schemas = True
        self.current_schema = ""
        self.query_one("#sidebar-title", Label).update("SCHEMAS")

        if not self.client.conn:
            self.add_message("Not connected — cannot load schemas", "error")
            return

        self.query_one(StatusBar).update_status("Loading schemas...", "query")
        schemas = self.client.get_schemas()

        lv = self.query_one("#list")
        lv.clear()

        if not schemas:
            lv.append(ListItem(Label("No schemas returned (check catalog access)")))
            self.add_message("No schemas found", "warning")
            self.query_one(StatusBar).update_status("No schemas found", "error")
        else:
            for s in schemas:
                lv.append(SchemaItem(s))
            self.add_message(f"Loaded {len(schemas)} schema(s)", "success")
            self.query_one(StatusBar).update_status(
                f"{len(schemas)} schema(s) — pick one; B: back to this list from tables", "success"
            )

        if focus_list:
            self.query_one("#list").focus()

    def load_tables_for_schema(self, schema: str, focus_list: bool = True) -> None:
        """Replace sidebar list with tables for the given schema."""
        schema = schema.strip().upper()
        if not schema:
            return

        self.sidebar_showing_schemas = False
        self.current_schema = schema
        self.query_one("#sidebar-title", Label).update(f"TABLES — {schema}")

        self.add_message(f"Loading tables from schema: {schema}", "info")
        self.query_one(StatusBar).update_status(f"Loading tables from {schema}...", "query")
        tables = self.client.get_tables(schema)

        lv = self.query_one("#list")
        lv.clear()

        if not tables:
            lv.append(ListItem(Label(f"No tables in {schema}")))
            self.add_message(f"No tables found in schema {schema}", "warning")
            self.query_one(StatusBar).update_status(f"No tables found in {schema}", "error")
        else:
            for t in tables:
                lv.append(TableItem(t))

            self.add_message(f"Loaded {len(tables)} table(s) from {schema}", "success")
            self.query_one(StatusBar).update_status(
                f"{len(tables)} table(s) in {schema} — B: back to schemas", "success"
            )

        if focus_list:
            self.query_one("#list").focus()

    @on(Input.Submitted, "#schema")
    def on_schema_input_submitted(self, event: Input.Submitted) -> None:
        """Optional: type a schema name and press Enter (same as choosing from the list)."""
        name = event.value.strip().upper()
        if not name:
            return
        self.load_tables_for_schema(name, focus_list=True)

    @on(ListView.Selected, "#list")
    def on_select(self, event):
        if isinstance(event.item, SchemaItem):
            self.load_tables_for_schema(event.item.schema_name, focus_list=True)
            return
        if isinstance(event.item, TableItem):
            self.current_table = event.item.table_name
            sql = f"SELECT * FROM {self.current_schema}.{self.current_table}"
            
            self.loaded_sql = ""
            self.loaded_file_name = ""
            
            self.query_one("#sql", TextArea).text = sql
            self.add_message(f"Loading table: {self.current_table}", "query")
            self.current_sql = sql
            self.current_offset = 0
            self.execute_query()

    def action_execute_sql(self):
        """Execute SQL from TextArea or loaded file (Ctrl+E)"""
        self.add_message("[Ctrl+E] Execution triggered", "info")
        
        if self.loaded_sql:
            sql = self.loaded_sql
            source = f"File: {self.loaded_file_name}"
        else:
            sql = self.query_one("#sql", TextArea).text.strip()
            source = "Editor"
        
        if not sql or sql.startswith("-- FILE LOADED:"):
            self.add_message("No SQL to execute", "error")
            return
        
        preview = sql[:80] + "..." if len(sql) > 80 else sql
        self.add_message(f"Source: {source} | Preview: {preview}", "info")
        
        # Check if multi-statement
        if ';' in sql :
            self._execute_multiple_statements(sql)
        else:
            # Single statement
            self.current_sql = sql
            self.current_offset = 0
            self.execute_query()
            
            # Auto-commit DDL/DML
            sql_upper = sql.strip().upper()
            if any(keyword in sql_upper.split() for keyword in ['CREATE', 'DROP', 'ALTER', 'INSERT', 'UPDATE', 'DELETE', 'GRANT', 'REVOKE']):
                status, msg = self.client.commit()
                self.add_message(f"Commit: {msg}", "success" if status else "error")
        
        # Clear loaded file after execution
        self.loaded_sql = ""
        self.loaded_file_name = ""

    def _is_simple_select(self, sql):
        """Check if SQL is a simple SELECT (safe for pagination)"""
        first_word = sql.strip().split()[0].upper()
        return first_word == 'SELECT' and sql.count(';') <= 1

    def _execute_multiple_statements(self, sql_script: str) -> None:
        """Execute every statement in order; commit DDL/DML on the fly."""
        statements = self.client._split_sql_statements(sql_script)
        total = len(statements)

        for idx, stmt in enumerate(statements, 1):
            # skip pure comment lines
            if stmt.startswith("--"):
                continue

            self.add_message(f"[{idx}/{total}]  {stmt[:70]}{'...' if len(stmt) > 70 else ''}", "query")

            try:
                self.client.cursor.execute(stmt.upper())

                # auto-commit for DDL/DML
                first = stmt.split()[0].upper()
                if first in ('CREATE', 'DROP', 'ALTER', 'INSERT', 'UPDATE', 'DELETE'):
                    self.client.commit()
                    self.add_message("  └─ committed", "success")

                # if SELECT, show row count and optionally load grid
                if self.client.cursor.description:
                    headers = [d[0] for d in self.client.cursor.description]
                    rows = self.client.cursor.fetchall()
                    self.add_message(f"  └─ returned {len(rows)} row(s)", "success")
                    # populate grid with **last** SELECT so user sees something
                    dt = self.query_one("#results-table")
                    dt.clear(columns=True)
                    dt.add_columns(*headers)
                    dt.add_rows(rows)

            except Exception as e:
                self.add_message(f"  └─ ERROR: {e}", "error")
                # stop on first failure (remove break to continue anyway)
                break
                
                
                

    def execute_query(self):
        """Execute current query with pagination"""
        self.add_message("Executing SQL query...", "query")
        self.query_one(StatusBar).update_status("Executing query...", "query")
        
        headers, rows, err = self.client.run_query_paginated(
            self.current_sql, 
            self.current_offset, 
            self.page_size
        )
        
        dt = self.query_one("#results-table")
        dt.clear(columns=True)
        
        if err:
            self.add_message(f"SQL Error: {err}", "error")
            self.query_one(StatusBar).update_status(f"Error: {err}", "error")
            return
        
        if headers:
            dt.add_columns(*headers)
            dt.add_rows(rows)
            
            self.total_rows = len(rows) + self.current_offset
            if len(rows) >= self.page_size:
                self.total_rows = (self.current_offset + len(rows)) * 2
            
            current_page = (self.current_offset // self.page_size) + 1
            total_pages = max(1, (self.total_rows + self.page_size - 1) // self.page_size)
            
            pg = self.query_one(PaginationBar)
            pg.current_page = current_page
            pg.total_pages = total_pages
            pg.page_size = self.page_size
            pg.total_rows = self.current_offset + len(rows)
            pg.update_display()
            
            self.add_message(f"Query successful - returned {len(rows)} rows", "success")
            self.query_one(StatusBar).update_status(f"Query returned {len(rows)} rows", "success")
        else:
            self.add_message("Query executed successfully (no results)", "info")
            self.query_one(StatusBar).update_status("Query executed (no results)", "info")

    def load_sql_without_execute(self, sql: str, source: str = "file"):
        """Load SQL into editor without executing"""
        self.query_one("#sql", TextArea).text = sql
        self.query_one("#sql").focus()
        
        self.loaded_sql = ""
        self.loaded_file_name = ""
        self.current_sql = ""
        self.current_offset = 0
        
        dt = self.query_one("#results-table")
        dt.clear(columns=True)

    def action_next_page(self):
        """Go to next page"""
        if not self.current_sql:
            return
        self.current_offset += self.page_size
        self.execute_query()

    def action_prev_page(self):
        """Go to previous page"""
        if not self.current_sql or self.current_offset == 0:
            return
        self.current_offset = max(0, self.current_offset - self.page_size)
        self.execute_query()

    def action_first_page(self):
        """Go to first page"""
        if not self.current_sql:
            return
        self.current_offset = 0
        self.execute_query()

    def action_last_page(self):
        """Go to last page (estimate)"""
        if not self.current_sql:
            return
        # This is an estimate - for accurate last page, we'd need COUNT(*)
        estimated_last_offset = max(0, self.total_rows - self.page_size)
        self.current_offset = estimated_last_offset
        self.execute_query()

    def action_change_page_size(self):
        """Change page size"""
        sizes = [10, 25, 50, 100, 200, 500]
        try:
            idx = sizes.index(self.page_size)
            self.page_size = sizes[(idx + 1) % len(sizes)]
        except ValueError:
            self.page_size = 50
        
        self.add_message(f"Page size changed to {self.page_size}", "info")
        self.query_one(StatusBar).update_status(f"Page size changed to {self.page_size}", "info")
        if self.current_sql:
            self.current_offset = 0
            self.execute_query()

    def action_refresh(self):
        """Refresh current query"""
        if self.current_sql:
            self.add_message("Refreshing query...", "info")
            self.execute_query()
        else:
            self.add_message("No query to refresh", "warning")
            self.query_one(StatusBar).update_status("No query to refresh", "info")

    def action_back_to_schemas(self):
        """Show catalog schema list in the sidebar (key B)."""
        if not self.client.conn:
            self.add_message("Not connected", "warning")
            return
        self.load_schema_list(focus_list=True)

    def action_clear_table(self):
        """Clear the results table"""
        self.query_one("#results-table").clear(columns=True)
        self.add_message("Results table cleared", "info")
        self.query_one(StatusBar).update_status("Table cleared", "info")
    
    def action_show_about(self):
        """Show about dialog"""
        self.push_screen(AboutScreen())
    
    def action_load_file(self):
        """Load SQL file (Ctrl+O)"""
        def handle_file_path(file_path: Optional[str]) -> None:
            if not file_path:
                return
            
            try:
                path = Path(file_path).expanduser()
                if not path.exists():
                    self.query_one(StatusBar).update_status(f"File not found: {file_path}", "error")
                    return
                
                if not path.is_file():
                    self.query_one(StatusBar).update_status(f"Not a file: {file_path}", "error")
                    return
                
                # Read the file
                with open(path, 'r', encoding='utf-8') as f:
                    sql_content = f.read()
                
                # Check if SQL is too large for editor
                if len(sql_content) > MAX_AUTO_EXECUTE_LENGTH:
                    # Store in memory, don't load to editor
                    self.loaded_sql = sql_content
                    self.loaded_file_name = path.name
                    
                    # Clear the editor and show indicator
                    self.query_one("#sql", TextArea).text = f"-- FILE LOADED: {path.name} ({len(sql_content)} chars)\n-- Press Ctrl+E to execute\n-- (File too large to display)"
                    
                    # Clear results
                    dt = self.query_one("#results-table")
                    dt.clear(columns=True)
                    
                    self.add_message(f"Loaded large file: {path.name} ({len(sql_content)} chars) - Press Ctrl+E to execute", "success")
                    self.query_one(StatusBar).update_status(
                        f"📁 Loaded: {path.name} ({len(sql_content)} chars) - Press Ctrl+E to execute", 
                        "success"
                    )
                else:
                    # Small file - load into editor
                    self.loaded_sql = ""
                    self.loaded_file_name = ""
                    self.load_sql_without_execute(sql_content, "file")
                    self.add_message(f"Loaded file: {path.name} ({len(sql_content)} chars)", "success")
                    self.query_one(StatusBar).update_status(
                        f"Loaded: {path.name} ({len(sql_content)} chars) - Press Ctrl+E to execute", 
                        "success"
                    )
                
                self.query_one("#sql").focus()
                
            except Exception as e:
                self.add_message(f"Error loading file: {e}", "error")
                self.query_one(StatusBar).update_status(f"Error loading file: {e}", "error")
        
        self.push_screen(LoadFileScreen(), handle_file_path)

if __name__ == "__main__":
    app = SqlApp()
    app.run()
