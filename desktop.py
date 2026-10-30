"""Goodframes desktop launcher: local server + native window (pywebview/WKWebView)."""
import multiprocessing
import socket
import threading
import time


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

    app.state.pick_folder = pick_folder
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    multiprocessing.freeze_support()  # photo analysis uses worker processes, also inside the .app
    main()
