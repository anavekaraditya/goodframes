"""Goodframes desktop launcher: local server + native window (pywebview/WKWebView)."""
import json
import multiprocessing
import os
import socket
import threading
import time


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_pickers = []  # an open share menu lives until it closes


def share_menu(window, path, rect):
    """The macOS share menu (AirDrop, Messages, Mail…) for one file, by the button that asked for it."""
    import AppKit
    from PyObjCTools import AppHelper

    def show():
        ns = window.native
        if ns is None:
            return
        view = ns.contentView()
        bounds = view.bounds()
        x, y, w, h = rect or (bounds.size.width / 2, bounds.size.height / 2, 1, 1)
        if not view.isFlipped():  # page pixels count down from the top; this view counts up
            y = bounds.size.height - y - h
        picker = AppKit.NSSharingServicePicker.alloc().initWithItems_([AppKit.NSURL.fileURLWithPath_(path)])
        _pickers[:] = [picker]
        picker.showRelativeToRect_ofView_preferredEdge_(((x, y), (w, h)), view, AppKit.NSMinYEdge)

    AppHelper.callAfter(show)


def main():
    import uvicorn
    import webview

    from photocull import settings
    from photocull.server.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)

    window = webview.create_window("Goodframes", f"http://127.0.0.1:{port}/", width=1360, height=880,
                                   min_size=(900, 620), background_color=settings.THEME_BACKGROUNDS[settings.load()["theme"]])

    def pick_folder():
        chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else None

    def on_drop(e):
        """A folder (or a photo inside one) dropped on the window opens as a shoot."""
        files = (e.get("dataTransfer") or {}).get("files") or []
        path = next((f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")), None)
        if path:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            window.evaluate_js(f"window.goodframes && window.goodframes.openDropped({json.dumps(folder)})")

    def bind():
        from webview.dom import DOMEventHandler
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

    def save_file(name):
        chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return chosen

    app.state.pick_folder = pick_folder
    app.state.save_file = save_file
    app.state.share = lambda path, rect: share_menu(window, path, rect)
    webview.start(bind)
    server.should_exit = True


if __name__ == "__main__":
    multiprocessing.freeze_support()  # photo analysis uses worker processes, also inside the .app
    main()
