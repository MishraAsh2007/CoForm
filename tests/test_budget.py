import pytest

from scienceclaw.budget import SpendGuard, BudgetedProvider
from scienceclaw.providers import ProviderError, ProviderResponse


class Fake:
    name, model = "openai", "gpt-6-luna"
    count = 0
    def complete_json(self, *args):
        self.count += 1
        return ProviderResponse({"ok": True}, 100, 50)


def test_persistent_limit_prevents_extra_calls_after_restart(tmp_path):
    path=tmp_path/"budget.sqlite"
    first=Fake()
    p=BudgetedProvider(first,SpendGuard(path,max_calls=2,max_per_provider=2))
    p.complete_json("json",{})
    p.complete_json("json",{})
    restarted=BudgetedProvider(first,SpendGuard(path,max_calls=2,max_per_provider=2))
    with pytest.raises(ProviderError,match="budget reached"):
        restarted.complete_json("json",{})
    assert first.count==2
    assert restarted.guard.summary()["calls"]==2


def test_dollar_limit_blocks_before_request(tmp_path):
    first=Fake()
    p=BudgetedProvider(first,SpendGuard(tmp_path/"cost.sqlite",max_usd=0.000001))
    with pytest.raises(ProviderError,match="budget reached"):
        p.complete_json("json",{})
    assert first.count==0


def test_timeout_keeps_cost_and_call_reservation(tmp_path):
    class Timeout(Fake):
        def complete_json(self,*args):
            raise ProviderError("request timed out")
    guard=SpendGuard(tmp_path/"cost.sqlite",max_calls=1)
    p=BudgetedProvider(Timeout(),guard)
    with pytest.raises(ProviderError):
        p.complete_json("json",{})
    record=guard.summary()["records"][0]
    assert record["charged_usd"]==record["reserved_usd"]>0
    with pytest.raises(ProviderError,match="budget reached"):
        p.complete_json("json",{})


def test_unknown_price_is_not_silently_used(tmp_path):
    first=Fake()
    first.model="unknown-expensive-model"
    p=BudgetedProvider(first,SpendGuard(tmp_path/"budget.sqlite"))
    with pytest.raises(ProviderError,match="no verified price"):
        p.complete_json("json",{})
    assert first.count==0
