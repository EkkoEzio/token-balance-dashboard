"""Flask 路由层。薄,业务委托给 scheduler / config。"""
from flask import Flask, request, jsonify, render_template

import config
import scheduler

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", theme=config.get_theme())


@app.route("/api/usage")
def usage():
    return jsonify({"providers": scheduler.get_all()})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    return jsonify({"providers": scheduler.refresh_now()})


@app.route("/api/config")
def get_config():
    """返回主题 + 各 provider 是否已配置 key(不泄露 key 值) + 关闭列表。"""
    keys = config.get_api_keys()
    return jsonify({
        "theme": config.get_theme(),
        "configured": {k: bool(v) for k, v in keys.items()},
        "disabled": sorted(config.get_disabled()),
    })


@app.route("/api/config/api-key", methods=["GET", "POST"])
def api_key():
    """GET 查看某 provider 已存的 key 明文(本轮不加密);POST 设置 key。"""
    if request.method == "GET":
        provider = request.args.get("provider", "")
        if not provider:
            return jsonify({"ok": False, "error": "缺少 provider"}), 400
        val = config.get_api_keys().get(provider, "")
        return jsonify({"ok": True, "key": val})

    body = request.get_json(force=True)
    provider = body.get("provider", "")
    key = body.get("key", "")
    if not provider:
        return jsonify({"ok": False, "error": "缺少 provider"}), 400
    r = config.set_api_key(provider, key)
    if r["ok"]:
        # 改了 key 立刻刷新该家
        scheduler.refresh_now()
    return jsonify(r)


@app.route("/api/config/theme", methods=["POST"])
def set_theme():
    body = request.get_json(force=True)
    theme = body.get("theme", "")
    return jsonify(config.set_theme(theme))


@app.route("/api/config/disabled", methods=["POST"])
def set_disabled():
    """设置某 provider 是否关闭。body: {provider, disabled: bool}。"""
    body = request.get_json(force=True)
    provider = body.get("provider", "")
    disabled = bool(body.get("disabled", False))
    if not provider:
        return jsonify({"ok": False, "error": "缺少 provider"}), 400
    r = config.set_disabled(provider, disabled)
    if r["ok"]:
        # 开关变化后立即重建并刷新(关闭的不再返回)
        scheduler.refresh_now()
    return jsonify(r)


if __name__ == "__main__":
    scheduler.start()
    app.run(host="127.0.0.1", port=config.PORT, debug=False)
