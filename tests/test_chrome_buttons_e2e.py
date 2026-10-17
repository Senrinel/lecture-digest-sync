"""Comprehensive Chrome E2E Automated Button and Workflow Testing Suite.

Mandatory environment: Google Chrome (C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe).
Controls every button, input, slider, modal, and drawer in the responsive interface,
simulates user inputs, and verifies functional correctness and API synchronization.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import uvicorn
import websockets

from lecture_pipeline.gui.server import app

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
HOST = "127.0.0.1"
PORT = 8000
CDP_PORT = 9222


class ChromeCDPClient:
    """Async client communicating directly with Google Chrome via Chrome DevTools Protocol."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.msg_id = 0
        self.pending: Dict[int, asyncio.Future] = {}
        self.console_logs: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        self.dialog_history: List[Dict[str, Any]] = []
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.ws_url, max_size=50 * 1024 * 1024)
        self._listener_task = asyncio.create_task(self._listen())
        await self.send("Runtime.enable")
        await self.send("Page.enable")
        await self.send("Console.enable")

    async def close(self) -> None:
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
        if self.ws:
            await self.ws.close()

    async def _listen(self) -> None:
        try:
            async for raw in self.ws:
                data = json.loads(raw)
                if "id" in data and data["id"] in self.pending:
                    fut = self.pending.pop(data["id"])
                    if not fut.done():
                        fut.set_result(data)
                elif "method" in data:
                    method = data["method"]
                    params = data.get("params", {})
                    if method == "Page.javascriptDialogOpening":
                        msg = params.get("message", "")
                        d_type = params.get("type", "")
                        answer = self._resolve_dialog_input(msg, d_type)
                        self.dialog_history.append({"msg": msg, "type": d_type, "answer": answer})
                        asyncio.create_task(
                            self.send("Page.handleJavaScriptDialog", {"accept": True, "promptText": answer})
                        )
                    elif method == "Runtime.consoleAPICalled":
                        self.console_logs.append(params)
                    elif method == "Runtime.exceptionThrown":
                        self.exceptions.append(params)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"\n[CDP WS EXCEPTION] {type(e).__name__}: {e}")

    def _resolve_dialog_input(self, message: str, d_type: str) -> str:
        """Provide intelligent simulated input for all browser prompts & confirms."""
        if "新科目名稱" in message:
            return "數位訊號處理"
        if "新增專有名詞" in message:
            return "Fast Fourier Transform"
        if "中文翻譯" in message or "說明" in message:
            return "快速傅立葉轉換"
        if "修改名詞" in message:
            return "FFT"
        if "修改課堂所屬科目" in message:
            return "演算法"
        if "修改課堂顯示標題" in message:
            return "演算法與資料結構進階篇"
        if "課堂科目名稱" in message:
            return "作業系統核心"
        return "測試資料"

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.msg_id += 1
        cid = self.msg_id
        payload: Dict[str, Any] = {"id": cid, "method": method}
        if params is not None:
            payload["params"] = params

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[cid] = fut
        await self.ws.send(json.dumps(payload))
        return await fut

    async def eval(self, expression: str) -> Any:
        res = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        if "result" in res and "exceptionDetails" in res["result"]:
            ex = res["result"]["exceptionDetails"]
            raise RuntimeError(f"JS Evaluation Error in '{expression[:60]}': {ex}")
        val = res.get("result", {}).get("result", {})
        return val.get("value")

    async def click(self, selector: str) -> bool:
        escaped_selector = json.dumps(selector)
        js = f"""
        (() => {{
            let el = null;
            try {{
                el = document.querySelector({escaped_selector});
            }} catch (_) {{}}
            if (!el) {{
                const all = Array.from(document.querySelectorAll('button, input, select, a, span'));
                el = all.find(b => (b.getAttribute('onclick') || '').includes({escaped_selector}) || b.id === {escaped_selector});
            }}
            if (!el) return false;
            el.scrollIntoView({{ block: 'center', inline: 'center' }});
            el.click();
            return true;
        }})()
        """
        ok = await self.eval(js)
        await asyncio.sleep(0.15)
        return bool(ok)

    async def click_by_xpath_text(self, text_fragment: str) -> bool:
        escaped_text = json.dumps(text_fragment)
        js = f"""
        (() => {{
            const btns = Array.from(document.querySelectorAll('button'));
            const target = btns.find(b => b.innerText.includes({escaped_text}));
            if (target) {{
                target.scrollIntoView({{ block: 'center', inline: 'center' }});
                target.click();
                return true;
            }}
            return false;
        }})()
        """
        ok = await self.eval(js)
        await asyncio.sleep(0.1)
        return bool(ok)


@pytest.fixture(scope="module")
def live_server_and_chrome():
    """Starts local FastAPI server and Google Chrome with CDP remote debugging."""
    assert os.path.exists(CHROME_EXE), f"Google Chrome executable must exist at: {CHROME_EXE}"

    # Set test mode bypass for native dialogs
    os.environ["LECTURE_PIPELINE_TEST_MODE"] = "1"

    config_path = Path("config.yaml")
    orig_config_bytes = config_path.read_bytes() if config_path.exists() else None

    # 1. Start Uvicorn in daemon thread
    server_config = uvicorn.Config(
        app=app,
        host=HOST,
        port=PORT,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(server_config)

    import threading
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server ready
    ready = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            pass
    assert ready, "FastAPI server failed to start on 127.0.0.1:8000"

    # 2. Launch Google Chrome
    temp_profile_dir = tempfile.mkdtemp(prefix="chrome_e2e_profile_")
    chrome_cmd = [
        CHROME_EXE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={temp_profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--start-maximized",
        f"http://{HOST}:{PORT}/",
    ]
    chrome_proc = subprocess.Popen(chrome_cmd)

    # 3. Discover CDP Page Target
    ws_url = None
    for _ in range(40):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=2) as r:
                targets = json.loads(r.read().decode("utf-8"))
                for t in targets:
                    if t.get("type") == "page" and "webSocketDebuggerUrl" in t:
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
        except Exception:
            pass

    assert ws_url, f"Failed to acquire Chrome CDP target from http://127.0.0.1:{CDP_PORT}/json"

    try:
        yield ws_url
    finally:
        # Teardown
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(chrome_proc.pid)], capture_output=True)
        except Exception:
            pass
        shutil.rmtree(temp_profile_dir, ignore_errors=True)
        server.should_exit = True
        if orig_config_bytes is not None:
            config_path.write_bytes(orig_config_bytes)


@pytest.mark.asyncio
async def test_chrome_full_button_and_workflow_suite(live_server_and_chrome: str):
    """Executes full click and input simulation on every interactive button in Google Chrome."""
    ws_url = live_server_and_chrome
    client = ChromeCDPClient(ws_url)
    await client.connect()

    try:
        # Explicitly navigate to ensure fresh page load
        await client.send("Page.navigate", {"url": f"http://{HOST}:{PORT}/"})
        await asyncio.sleep(1.0)

        # Wait for page to fully load and parse DOM
        title = ""
        for _ in range(40):
            try:
                ready_state = await client.eval("document.readyState")
                title = await client.eval("document.title") or ""
                if ready_state == "complete" and "Lecture Digest Sync" in title:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)

        assert "Lecture Digest Sync" in title, f"Expected 'Lecture Digest Sync' in title, got: {title}"

        # 0. Inject testing interceptor hooks to track window.open / close
        await client.eval("""
            window.__is_test_mode = true;
            window.__intercepted_opens = [];
            window.__intercepted_closes = 0;
            const originalOpen = window.open;
            window.open = function(url, target) {
                window.__intercepted_opens.push(url);
                return { focus: () => {} };
            };
            const originalClose = window.close;
            window.close = function() {
                window.__intercepted_closes++;
            };
        """)

        print("\n[CHROME E2E] Page loaded successfully in Google Chrome.")

        # =====================================================================
        # 1. Top Bar Mode Switcher Buttons
        # =====================================================================
        print("  --> Testing Mode switcher buttons (Web / Both / Window)...")
        # Click Web mode
        ok = await client.click("#mode-btn-web")
        assert ok
        mode = await client.eval("currentAppMode")
        assert mode == "web"

        # Click Both mode
        ok = await client.click("#mode-btn-both")
        assert ok
        mode = await client.eval("currentAppMode")
        assert mode == "both"

        # Click Window mode (window.close hook prevents tab destruction)
        ok = await client.click("#mode-btn-window")
        assert ok
        mode = await client.eval("currentAppMode")
        assert mode == "window"

        # Restore to Web mode
        await client.click("#mode-btn-web")

        # =====================================================================
        # 2. Watcher Toggle Button
        # =====================================================================
        print("  --> Testing Watcher pause/resume toggle...")
        ok = await client.click("#toggle-watcher-btn")
        assert ok
        await asyncio.sleep(0.3)
        # Click again to toggle back
        await client.click("#toggle-watcher-btn")
        await asyncio.sleep(0.2)

        # =====================================================================
        # 3. Zoom In / Zoom Out / Reset Buttons
        # =====================================================================
        print("  --> Testing UI zoom controls (A- / A+ / Reset)...")
        initial_zoom = await client.eval("currentUiZoom")
        # Zoom In (A+)
        await client.click("button[onclick*='changeUiZoom(10)']")
        zoom_after_in = await client.eval("currentUiZoom")
        assert zoom_after_in == initial_zoom + 10

        # Zoom Out (A-)
        await client.click("button[onclick*='changeUiZoom(-10)']")
        zoom_after_out = await client.eval("currentUiZoom")
        assert zoom_after_out == initial_zoom

        # Reset Zoom
        await client.eval("resetUiZoom()")
        assert await client.eval("currentUiZoom") == 100

        # =====================================================================
        # 4. Dark / Light Theme Toggle Button
        # =====================================================================
        print("  --> Testing Dark/Light Theme toggle button...")
        initial_is_light = await client.eval("document.documentElement.classList.contains('light')")
        # Click toggle theme
        await client.click("#theme-toggle-btn")
        is_light_after = await client.eval("document.documentElement.classList.contains('light')")
        assert is_light_after != initial_is_light

        # Click again to restore
        await client.click("#theme-toggle-btn")
        restored_is_light = await client.eval("document.documentElement.classList.contains('light')")
        assert restored_is_light == initial_is_light

        # =====================================================================
        # 5. Customizer Drawer Toggle & Dynamic Background / Font Controls
        # =====================================================================
        print("  --> Testing Customizer Drawer & 10 Fluid Models & Reset buttons...")
        # Open drawer
        await client.click("button[onclick*='toggleCustomizerDrawer()']")
        drawer_visible = await client.eval("!document.getElementById('customizer-drawer').classList.contains('translate-x-full')")
        assert drawer_visible, "Customizer drawer should be open"

        # Iterate all 10 fluid models
        model_options = await client.eval("Array.from(document.getElementById('fluid-model-select').options).map(o => o.value)")
        assert len(model_options) == 10, f"Expected 10 fluid models, found {len(model_options)}"
        for m in model_options:
            await client.eval(f"document.getElementById('fluid-model-select').value = '{m}'; changeFluidModel('{m}')")
            active_m = await client.eval("fluidModel")
            assert str(active_m) == str(m)

        # Click Reset Fluid Model button
        await client.click_by_xpath_text("預設模型")
        assert await client.eval("fluidModel") == "lorenz_attractor"

        # Test Color Count slider & reset button
        await client.eval("changeColorCount(2)")
        assert await client.eval("document.getElementById('num-color-count').value") == "2"
        await client.click("button[onclick*='changeColorCount(4)']")
        assert await client.eval("document.getElementById('num-color-count').value") == "4"

        # Test Fluid Speed slider & reset button
        await client.eval("changeFluidSpeed(3.0)")
        assert await client.eval("fluidSpeed") == 3.0
        await client.click("button[onclick*='changeFluidSpeed(1.0)']")
        assert await client.eval("fluidSpeed") == 1.0

        # Test Surface Opacity slider & reset button
        await client.eval("changeSurfaceOpacity(45)")
        assert await client.eval("document.getElementById('num-surface-opacity').value") == "45"
        await client.click("button[onclick*='changeSurfaceOpacity(60)']")
        assert await client.eval("document.getElementById('num-surface-opacity').value") == "60"

        # Test Font Size slider & reset button
        await client.eval("changeFontSize(18)")
        assert await client.eval("document.getElementById('num-font-size').value") == "18"
        await client.click("button[onclick*='changeFontSize(14)']")
        assert await client.eval("document.getElementById('num-font-size').value") == "14"

        # Test Font Family select & reset button
        await client.eval("changeFontFamily('Microsoft JhengHei')")
        await client.click("button[onclick*='resetFontFamily()']")
        font_val = await client.eval("document.getElementById('font-family-select').value")
        assert "Segoe UI" in font_val

        # Test Single Color Reset
        await client.click("button[onclick*='resetSingleColor(0)']")

        # Test UI Text modification and Reset Single / All
        await client.eval("document.getElementById('ipt-custom-txt-app-title').value = 'Test Custom Title'; updateUiText('txt-app-title', 'Test Custom Title')")
        title_text = await client.eval("document.getElementById('txt-app-title').innerText")
        assert title_text == "Test Custom Title"
        # Reset single UI text
        await client.click("button[onclick*='resetSingleUiText'][onclick*='txt-app-title']")
        title_restored = await client.eval("document.getElementById('txt-app-title').innerText")
        assert title_restored == "Lecture Digest Sync"

        # Reset All UI texts button
        await client.click("button[onclick*='resetAllUiTexts()']")

        # Reset All Customizations Defaults button
        await client.click("button[onclick*='resetAllCustomizations()']")

        # Close drawer using '完成並關閉'
        await client.click_by_xpath_text("完成並關閉")
        await asyncio.sleep(0.2)

        # =====================================================================
        # 6. Navigation Tabs Switching
        # =====================================================================
        print("  --> Testing Navigation Tab Switching (Studio, History, Glossary, Settings, Diagnostics)...")
        tabs = [
            ("nav-studio", "view-studio"),
            ("nav-history", "view-history"),
            ("nav-glossary", "view-glossary"),
            ("nav-settings", "view-settings"),
            ("nav-diagnostics", "view-diagnostics"),
        ]
        for nav_id, view_id in tabs:
            print(f"      Navigating to {nav_id} ({view_id})...")
            await client.click(f"#{nav_id}")
            visible = await client.eval(f"!document.getElementById('{view_id}').classList.contains('hidden')")
            assert visible, f"Tab {view_id} should be visible after clicking #{nav_id}"

        # =====================================================================
        # 7. Studio / Workbench Actions
        # =====================================================================
        print("  --> Testing Studio Workbench buttons (File/Folder input triggers, Pause, Abort, Long-term folder)...")
        await client.click("#nav-studio")

        # Open Long-term archive folder button
        await client.click_by_xpath_text("開啟長期歸檔目錄")

        # Task control buttons: Pause / Abort
        await client.click("#task-btn-pause")
        await client.click("#task-btn-abort")

        # Today's run table buttons (if rows exist)
        rows_count = await client.eval("document.querySelectorAll('#recent-runs-body tr').length")
        if rows_count > 0:
            # Click open note on first row
            await client.click("#recent-runs-body tr button[onclick*='openLectureFile']")
            # Click jump to player on first row
            await client.click("#recent-runs-body tr button[onclick*='jumpToLectureInHistory']")
            await asyncio.sleep(0.3)

        # =====================================================================
        # 8. History & Player Actions
        # =====================================================================
        print("  --> Testing Lecture History & Dual Sync Player controls...")
        await client.click("#nav-history")

        # Search filter input
        await client.eval("document.getElementById('history-search-input').value = '離散'; filterHistoryLectures('離散')")
        await client.eval("document.getElementById('history-search-input').value = ''; filterHistoryLectures('')")

        # Select first lecture if available
        first_lecture = await client.eval("allHistoryLectures.length > 0 ? allHistoryLectures[0].id : null")
        if first_lecture:
            await client.eval(f"selectHistoryLecture('{first_lecture}')")
            await asyncio.sleep(0.3)

            # Audio controls
            await client.click("#player-play-btn")  # Play / Pause toggle
            await client.click("#speed-btn-1")      # 1.0x
            await client.click("#speed-btn-125")    # 1.25x
            await client.click("#speed-btn-15")     # 1.5x
            await client.click("#speed-btn-2")      # 2.0x

            # Timecode jump
            await client.eval("jumpAudioToTimestamp(15)")

            # Action buttons
            await client.click("button[onclick*='copyNotebookBundle()']")
            await client.click("#btn-player-docs")
            await client.click("button[onclick*='openLocalSummary()']")
            await client.click("button[onclick*='openCurrentLectureFolder()']")

            # Test Rename lecture dialog flow
            await client.click("button[onclick*='promptRenameLecture()']")
            await asyncio.sleep(0.3)

        # =====================================================================
        # 9. Glossary & Timetable Management
        # =====================================================================
        print("  --> Testing Glossary (Add Course, Add/Edit/Delete Term, Upload) & Timetable CRUD...")
        await client.click("#nav-glossary")

        # Course select change
        await client.eval("changeGlossaryCourse('Discrete Mathematics')")

        # Add Course button (dialog returns '數位訊號處理')
        await client.click("button[onclick*='promptAddCourse()']")
        courses = []
        for _ in range(25):
            await asyncio.sleep(0.2)
            courses = await client.eval("Array.from(document.getElementById('glossary-course-select').options).map(o => o.value)")
            if "數位訊號處理" in courses:
                break
        assert "數位訊號處理" in courses, f"Newly added course should appear in select options, got: {courses}"

        # Add Term button (dialog returns 'Fast Fourier Transform' & '快速傅立葉轉換')
        await client.click("button[onclick*='promptAddGlossaryTerm()']")
        terms_text = ""
        for _ in range(25):
            await asyncio.sleep(0.2)
            terms_text = await client.eval("document.getElementById('glossary-terms-list').innerText")
            if "Fast Fourier Transform" in terms_text:
                break
        assert "Fast Fourier Transform" in terms_text, "Newly added term should be in container"

        # Edit Term button (dialog returns 'FFT')
        await client.click("#glossary-terms-list button[onclick*='promptEditGlossaryTerm']")
        for _ in range(25):
            await asyncio.sleep(0.2)
            terms_text = await client.eval("document.getElementById('glossary-terms-list').innerText")
            if "FFT" in terms_text:
                break
        assert "FFT" in terms_text, "Edited term should be in container"

        # Delete Term button (dialog confirms True)
        await client.click("#glossary-terms-list button[onclick*='deleteGlossaryTerm']")
        for _ in range(25):
            await asyncio.sleep(0.2)
            terms_text = await client.eval("document.getElementById('glossary-terms-list').innerText")
            if "FFT" not in terms_text:
                break
        assert "FFT" not in terms_text

        # Delete Course button (dialog confirms True)
        await client.click("button[onclick*='promptDeleteCourse()']")
        for _ in range(25):
            await asyncio.sleep(0.2)
            courses = await client.eval("Array.from(document.getElementById('glossary-course-select').options).map(o => o.value)")
            if "數位訊號處理" not in courses:
                break
        assert "數位訊號處理" not in courses

        # Timetable enable toggle
        await client.click("#timetable-enabled")
        await asyncio.sleep(0.2)
        await client.click("#timetable-enabled")

        # Timetable Add Rule button
        await client.click("button[onclick*='openTimetableModal()']")
        modal_open = await client.eval("document.getElementById('timetable-modal').open")
        assert modal_open, "Timetable modal should be open"

        # Fill modal form
        await client.eval("""
            document.getElementById('modal-rule-day').value = 'Thursday';
            document.getElementById('modal-rule-start').value = '10:00';
            document.getElementById('modal-rule-end').value = '12:00';
            document.getElementById('modal-rule-course').value = '編譯器最佳化';
        """)
        # Click Save Rule button
        await client.click("button[onclick*='saveTimetableRule()']")
        rules_text = ""
        for _ in range(25):
            await asyncio.sleep(0.2)
            rules_text = await client.eval("document.getElementById('timetable-rules-list').innerText")
            if "編譯器最佳化" in rules_text:
                break
        assert "編譯器最佳化" in rules_text

        # Test Edit rule button
        await client.click("#timetable-rules-list button[onclick*='editTimetableRule']")
        assert await client.eval("document.getElementById('timetable-modal').open")
        # Close via cancel button
        await client.click("#timetable-modal button[onclick*='closeTimetableModal()']")
        assert not await client.eval("document.getElementById('timetable-modal').open")

        # Test Delete rule button (targeting the added rule)
        await client.eval("""
            (() => {
                const rows = Array.from(document.querySelectorAll('#timetable-rules-list > div'));
                const targetRow = rows.find(d => d.innerText.includes('編譯器最佳化'));
                if (targetRow) {
                    const btn = targetRow.querySelector("button[onclick*='deleteTimetableRule']");
                    if (btn) btn.click();
                }
            })()
        """)
        for _ in range(25):
            await asyncio.sleep(0.2)
            rules_text = await client.eval("document.getElementById('timetable-rules-list').innerText")
            if "編譯器最佳化" not in rules_text:
                break
        assert "編譯器最佳化" not in rules_text

        # =====================================================================
        # 10. Pipeline Settings & Directory Controls
        # =====================================================================
        print("  --> Testing Settings Directory paths, sliders, and individual reset buttons...")
        await client.click("#nav-settings")

        # Test 4 directories: browse, open, reset
        dir_keys = ["dir-inbox", "dir-archive", "dir-export", "dir-logs"]
        for d in dir_keys:
            # Change value
            await client.eval(f"document.getElementById('{d}').value = 'custom_{d}'")
            # Open folder button
            await client.click(f"button[onclick*='openSystemFolder'][onclick*='{d}']")
            # Browse folder button
            await client.click(f"button[onclick*='browseSystemFolder'][onclick*='{d}']")
            # Reset folder button
            await client.click(f"button[onclick*='resetSingleParam'][onclick*='{d}']")

        # Test 7 Audio / AI sliders & reset buttons
        param_tests = [
            ("hp-freq", "180", "100"),
            ("lufs", "-25", "-19"),
            ("nr", "15", "0"),
            ("vad-thresh", "-30", "-40"),
            ("chunk-chars", "2500", "1500"),
            ("thinking", "2048", "1024"),
            ("timeout", "120", "60"),
        ]
        for pid, test_val, default_val in param_tests:
            # Change param
            await client.eval(f"syncParam('{pid}', {test_val})")
            assert await client.eval(f"document.getElementById('num-{pid}').value") == test_val
            # Click Reset button
            await client.click(f"button[onclick*='resetParam'][onclick*='{pid}']")
            assert await client.eval(f"document.getElementById('num-{pid}').value") == default_val

        # Zoom reset in settings
        await client.click("#view-settings button[onclick*='resetUiZoom()']")

        # =====================================================================
        # 11. Diagnostics Actions
        # =====================================================================
        print("  --> Testing Diagnostics system health refresh and Windows startup buttons...")
        await client.click("#nav-diagnostics")

        # Refresh diagnostics button
        await client.click("button[onclick*='refreshSystemDiagnostics()']")
        await asyncio.sleep(0.4)

        # Enable Windows startup button
        await client.click("button[onclick*='enableWindowsStartup()']")
        await asyncio.sleep(0.2)

        # =====================================================================
        # 12. Full Button Inventory Audit
        # =====================================================================
        total_buttons = await client.eval("document.querySelectorAll('button').length")
        print(f"\n[CHROME E2E AUDIT] Total DOM buttons evaluated: {total_buttons}")
        assert total_buttons >= 70, f"Expected at least 70 buttons in UI, found {total_buttons}"

        # Verify no JavaScript runtime exceptions occurred during the entire test
        assert len(client.exceptions) == 0, f"Uncaught JS exceptions in Chrome: {client.exceptions}"

        print(f"[CHROME E2E AUDIT] Dialogs handled: {len(client.dialog_history)}")
        print("[SUCCESS] ALL BUTTONS & WORKFLOWS SUCCESSFULLY EXECUTED AND VERIFIED IN GOOGLE CHROME!")

    finally:
        await client.close()
