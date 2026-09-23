"""试算平衡表只读接口的端到端测试。"""
from __future__ import annotations


def _account(client, code, name, currency="CNY", atype="asset"):
    r = client.post("/accounts", json={
        "code": code, "name": name, "currency": currency, "type": atype,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _post(client, rid, lines, *, currency="CNY", vdate="2026-09-20"):
    r = client.post("/vouchers", json={
        "request_id": rid, "date": vdate, "currency": currency, "lines": lines,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _seed_book(client):
    bank = _account(client, "1002", "银行存款-人民币")
    income = _account(client, "4001", "科研拨款", atype="income")
    expense = _account(client, "5001", "设备费", atype="expense")
    exp2 = _account(client, "5002", "材料费", atype="expense")
    bank_usd = _account(client, "1003", "银行存款-美元", currency="USD")
    income_usd = _account(client, "4002", "美元拨款", currency="USD", atype="income")

    # 8 月：拨款 100
    _post(client, "tb-1", [
        {"account_id": bank["id"], "direction": "debit", "amount": "100.00"},
        {"account_id": income["id"], "direction": "credit", "amount": "100.00"},
    ], vdate="2026-08-10")
    # 9 月：费用 30
    _post(client, "tb-2", [
        {"account_id": expense["id"], "direction": "debit", "amount": "30.00"},
        {"account_id": bank["id"], "direction": "credit", "amount": "30.00"},
    ], vdate="2026-09-05")
    # 9 月：一笔错误凭证随后在当月冲销，相关科目净额归零
    wrong = _post(client, "tb-3", [
        {"account_id": exp2["id"], "direction": "debit", "amount": "20.00"},
        {"account_id": bank["id"], "direction": "credit", "amount": "20.00"},
    ], vdate="2026-09-06")
    r = client.post(f"/vouchers/{wrong['id']}/reverse", json={
        "request_id": "tb-3-rev", "date": "2026-09-07",
    })
    assert r.status_code == 201, r.text
    # 9 月：美元凭证 50
    _post(client, "tb-4", [
        {"account_id": bank_usd["id"], "direction": "debit", "amount": "50.00"},
        {"account_id": income_usd["id"], "direction": "credit", "amount": "50.00"},
    ], currency="USD", vdate="2026-09-08")
    return {
        "bank": bank, "income": income, "expense": expense, "exp2": exp2,
        "bank_usd": bank_usd, "income_usd": income_usd,
    }


def _lines_by_code(group):
    return {ln["account_code"]: ln for ln in group["lines"]}


def test_trial_balance_cumulative_grouped_and_balanced(client):
    accs = _seed_book(client)

    tb = client.get("/reports/trial-balance", params={"period": "2026-09"}).json()
    assert tb["period"] == "2026-09"
    assert tb["generated_at"]

    by_ccy = {g["currency"]: g for g in tb["groups"]}
    assert set(by_ccy) == {"CNY", "USD"}

    cny = by_ccy["CNY"]
    lines = _lines_by_code(cny)
    # 净额为 0 的材料费科目不列出
    assert "5002" not in lines
    # 银行：借 120 (100 + 冲销 20)、贷 50 (30 + 20)，期末借方 70
    bank = lines["1002"]
    assert bank["total_debit"] == "120.00"
    assert bank["total_credit"] == "50.00"
    assert bank["balance_direction"] == "debit"
    assert bank["ending_balance"] == "70.00"
    # 收入：期末贷方 100
    assert lines["4001"]["balance_direction"] == "credit"
    assert lines["4001"]["ending_balance"] == "100.00"
    # 费用：期末借方 30
    assert lines["5001"]["balance_direction"] == "debit"
    assert lines["5001"]["ending_balance"] == "30.00"
    # 组内按科目代码排序
    assert [ln["account_code"] for ln in cny["lines"]] == ["1002", "4001", "5001"]
    # 期末借贷合计相等：借 70+30 = 贷 100
    assert cny["total_debit"] == "100.00"
    assert cny["total_credit"] == "100.00"
    assert cny["balanced"] is True

    usd = by_ccy["USD"]
    usd_lines = _lines_by_code(usd)
    assert usd_lines["1003"]["ending_balance"] == "50.00"
    assert usd_lines["1003"]["balance_direction"] == "debit"
    assert usd_lines["4002"]["ending_balance"] == "50.00"
    assert usd_lines["4002"]["balance_direction"] == "credit"
    assert usd["total_debit"] == usd["total_credit"] == "50.00"
    assert usd["balanced"] is True


def test_trial_balance_cutoff_excludes_later_periods(client):
    accs = _seed_book(client)

    tb = client.get("/reports/trial-balance", params={"period": "2026-08"}).json()
    assert [g["currency"] for g in tb["groups"]] == ["CNY"]
    cny = tb["groups"][0]
    codes = {ln["account_code"] for ln in cny["lines"]}
    # 9 月才发生的设备费与美元业务不出现
    assert codes == {"1002", "4001"}
    assert cny["total_debit"] == cny["total_credit"] == "100.00"
    assert cny["balanced"] is True

    # 早于全部业务的截止期间：空表、200
    early = client.get("/reports/trial-balance", params={"period": "2026-07"}).json()
    assert early["groups"] == []


def test_trial_balance_invalid_period(client):
    for bad in ("2026-9", "2026-13", "abc", "2026-09-01"):
        r = client.get("/reports/trial-balance", params={"period": bad})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "unprocessable_entity"


def test_trial_balance_is_read_only(client):
    _seed_book(client)

    before_vouchers = client.get("/vouchers", params={"limit": 500}).json()["items"]
    before_periods = client.get("/periods").json()

    # 查一个尚不存在的期间，不应自动建期间
    for period in ("2026-12", "2026-09"):
        r = client.get("/reports/trial-balance", params={"period": period})
        assert r.status_code == 200, r.text

    after_vouchers = client.get("/vouchers", params={"limit": 500}).json()["items"]
    after_periods = client.get("/periods").json()
    assert [v["id"] for v in after_vouchers] == [v["id"] for v in before_vouchers]
    assert after_periods == before_periods
    assert all(p["key"] != "2026-12" for p in after_periods)

    # 各科目余额与逐笔分类账保持不变
    bank = next(a for a in client.get("/accounts").json() if a["code"] == "1002")
    balances = client.get(f"/accounts/{bank['id']}/balances").json()
    assert {(b["period_key"], b["total_debit"], b["total_credit"]) for b in balances} == {
        ("2026-08", "100.00", "0.00"),
        ("2026-09", "20.00", "50.00"),
    }
