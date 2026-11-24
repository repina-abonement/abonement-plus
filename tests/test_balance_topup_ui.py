from pathlib import Path


js = (Path(__file__).parents[1] / "crm/static/app.js").read_text()

for text in ("+ Пополнить баланс", "Новая оплата — деньги получены сейчас",
             "Старые данные — было оплачено ранее", "Картой на филиале через терминал Сбера",
             "/balance-topup", "Выставленные счета не оплачиваются автоматически"):
    assert text in js
