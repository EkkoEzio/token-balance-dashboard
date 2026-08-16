"""全局测试隔离:任何测试都不得产生真实副作用。"""
import pytest

import config
import scheduler


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch, tmp_path):
    # 1) 禁止真实 macOS 桌面通知。
    #    教训:曾有测试经 refresh_now() → _check_and_notify() 用假数据(data={})
    #    触发真实「余额仅剩 ¥0.00」通知,弹到用户屏幕上。
    monkeypatch.setattr(scheduler, "_send_notification",
                        lambda title, msg, level: None)
    # 2) 数据目录重定向到临时目录,防止 notified.json/cache.json 写入真实 data/。
    #    CONFIG_FILE 是 import 时算出的常量,需单独重定向。
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
