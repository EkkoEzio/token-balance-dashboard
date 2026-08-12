"""Flask 路由层。薄,业务委托给 scheduler / config。"""
import time
from flask import Flask, request, jsonify, render_template

import config
import scheduler

app = Flask(__name__)

# 限流防护:各刷新入口的冷却时间(秒)。
# 防止用户(或多标签页)狂点刷新按钮 → 后端频繁打 Tavly → 触发 429。
_COOLDOWN_TAVLY_ONE = 30       # Tavly 单卡刷新 30 秒冷却
_COOLDOWN_TAVLY_ALL = 60       # 全局刷新里包含 Tavly 时 60 秒冷却(全量更重)
_COOLDOWN_OTHER_ONE = 5        # 其他 provider 单卡刷新 5 秒冷却
_COOLDOWN_OTHER_ALL = 10       # 全局刷新 10 秒冷却
_last_refresh_ts: dict = {}    # {key_or_'__all__': float(monotonic)}


@app.route("/")
def index():
    return render_template("index.html", theme=config.get_theme())


@app.route("/api/usage")
def usage():
    return jsonify({
        "providers": scheduler.get_all(),
        "last_refresh": scheduler.last_refresh_ts(),
    })


@app.route("/api/refresh", methods=["POST"])
def refresh():
    # 全局刷新 cooldown:防止狂点触发各 provider 风控
    cooldown = _COOLDOWN_TAVLY_ALL if _is_active("tavly") else _COOLDOWN_OTHER_ALL
    block = _check_cooldown("__all__", cooldown)
    if block:
        return jsonify(block), 429
    return jsonify({
        "providers": scheduler.refresh_now(),
        "last_refresh": scheduler.last_refresh_ts(),
    })


@app.route("/api/refresh/<key>", methods=["POST"])
def refresh_one(key):
    """只刷新单个 provider(卡片单独刷新按钮)。
    Tavly 单独刷新 30 秒冷却(防限流);其他 provider 5 秒冷却。"""
    cooldown = _COOLDOWN_TAVLY_ONE if key == "tavly" else _COOLDOWN_OTHER_ONE
    block = _check_cooldown(key, cooldown)
    if block:
        return jsonify(block), 429
    result = scheduler.refresh_one(key)
    if result is None:
        return jsonify({"ok": False, "error": f"未知的供应商: {key}"}), 404
    return jsonify({"ok": True, "result": result})


def _is_active(key: str) -> bool:
    """某 provider 是否启用(未关闭)。关闭的不参与 cooldown 判断。"""
    return key not in config.get_disabled()


def _check_cooldown(key: str, cooldown_sec: int) -> dict | None:
    """冷却检查。冷却期内返回 429 + 剩余秒数;否则放行并打点。
    返回 None 表示可以继续;返回 dict 表示被 cooldown 拦截。"""
    now = time.monotonic()
    last = _last_refresh_ts.get(key, 0.0)
    remain = cooldown_sec - (now - last)
    if remain > 0:
        return {
            "ok": False,
            "error": f"刷新过于频繁,请 {int(remain) + 1} 秒后再试",
            "cooldown_remaining": int(remain) + 1,
        }
    _last_refresh_ts[key] = now
    return None


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
    """POST 设置 key。GET 不再明文返回(改用 verify 接口需主密码)。"""
    if request.method == "GET":
        return jsonify({"ok": False, "error": "请用 /api/config/verify 接口"}), 405

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


@app.route("/api/config/verify", methods=["POST"])
def verify_and_copy():
    """验证主密码后返回某 provider 的 key 明文(供复制)。
    body: {provider, password}。未设主密码或密码错都返回失败。"""
    body = request.get_json(force=True)
    provider = body.get("provider", "")
    password = body.get("password", "")
    if not provider:
        return jsonify({"ok": False, "error": "缺少 provider"}), 400
    if not config.has_master_password():
        return jsonify({"ok": False, "error": "未设置主密码,请先在设置里配置"})
    if not config.check_master_password(password):
        return jsonify({"ok": False, "error": "主密码错误"})
    val = config.get_api_keys().get(provider, "")
    return jsonify({"ok": bool(val), "key": val})


@app.route("/api/config/master-password", methods=["POST"])
def set_master_pw():
    """设置/修改主密码。body: {password}。"""
    body = request.get_json(force=True)
    return jsonify(config.set_master_password(body.get("password", "")))


@app.route("/api/config/master-password-status")
def master_pw_status():
    return jsonify({"has_master_password": config.has_master_password()})


@app.route("/api/config/theme", methods=["POST"])
def set_theme():
    body = request.get_json(force=True)
    theme = body.get("theme", "")
    return jsonify(config.set_theme(theme))


@app.route("/api/config/order", methods=["GET", "POST"])
def order():
    """GET 返回卡片顺序;POST 保存顺序(provider key 数组)。"""
    if request.method == "GET":
        return jsonify({"ok": True, "order": config.get_order()})
    body = request.get_json(force=True)
    order_list = body.get("order", [])
    if not isinstance(order_list, list):
        return jsonify({"ok": False, "error": "order 需为数组"}), 400
    return jsonify(config.set_order(order_list))


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


@app.route("/api/alerts")
def alerts():
    """返回当前触发的告警 + 告警配置。"""
    return jsonify({
        "alerts": scheduler.evaluate_alerts(scheduler.get_all()),
        "config": config.get_alerts_config(),
    })


@app.route("/api/alerts/config", methods=["POST"])
def set_alerts_config():
    """更新告警配置。body: {enabled?, threshold_balance?, threshold_pct?}。"""
    body = request.get_json(force=True)
    return jsonify(config.set_alerts_config(body))


if __name__ == "__main__":
    scheduler.start()
    app.run(host="127.0.0.1", port=config.PORT, debug=False)
