from steam_market_sentinel.alerts import render_alert
from steam_market_sentinel.models import Alert


def _alert(rule: str) -> tuple[str, str]:
    return render_alert(Alert(1, 730, "Revolution Case", "Revolution Case", rule,
                              1234, 1500, "CNY"))


def test_each_rule_has_its_own_wording():
    target_subject, target_body = _alert("target")
    low_subject, low_body = _alert("new_low")
    drop_subject, drop_body = _alert("drop")

    assert target_subject.startswith("[Steam 目标价]")
    assert low_subject.startswith("[Steam 刷新史低]")
    assert drop_subject.startswith("[Steam 价格下降]")
    assert "目标价" in target_body
    assert "史低" in low_body
    assert "比上一次采样" in drop_body
    # Every body carries both amounts and the honesty note.
    for _subject, body in (("", target_body), ("", low_body), ("", drop_body)):
        assert "¥12.34" in body and "¥15.00" in body
        assert "并非 Steam 全平台历史最低价" in body
