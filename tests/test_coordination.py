from copy import deepcopy

import pytest

from coform.agents import decide, validate_proposal, Proposal
from coform.domain import Geometry, ModelParameters, Protocol
from coform.providers import ProviderError, ProviderResponse


def evidence_context(**protocol_changes):
    return {"observations":[{"id":"obs", "candidate_id":"candidate", "purpose":"initial", "source":"simulated", "force_n":.3,"load_position_m":.12,"displacement_m":.001,"uncertainty_m":.00001}],
            "candidates":[{"id":"candidate","geometry":Geometry().model_dump()}],
            "model":{"id":"model","parameters":ModelParameters().model_dump(),"fit":{"status":"insufficient"}},
            "protocol":Protocol(**protocol_changes).model_dump(),"budget":{"cycles_remaining":8,"designs_remaining":3,"tokens_remaining":12000},
            "evidence_ids":["obs","candidate","model"],"agent_states":{},
            "tools":{"diagnostic_position_m":.06,"recommended_geometry":None,"predictions":[]}}


class ScriptedPeer:
    name="scripted"
    model="local-contract-test"
    def __init__(self, role, *, improve=False, abstain=False, duplicate=False, unknown=False, malformed=False):
        self.role,self.improve,self.abstain,self.duplicate,self.unknown,self.malformed=role,improve,abstain,duplicate,unknown,malformed
        self.calls=[]
        self.round=0
    def complete_json(self,system,payload,max_output_tokens):
        self.calls.append(deepcopy(payload))
        if "proposals" not in payload:
            self.round+=1
            proposal={"action":"experiment","candidate_id":"candidate","force_n":.3,"load_position_m":.06,
                      "reason":"Separated station","evidence_ids":["obs"],"cost":{"cycles":1,"designs":0}}
            return ProviderResponse({"proposal":None if self.abstain else [] if self.malformed else proposal},20,20)
        support=self.improve and self.round>1
        votes=[{"proposal_id":p["id"],"support":support,"reason":f"{self.role} needs another round", "evidence_ids":["invented" if self.unknown else "obs"]} for p in payload["proposals"]]
        if self.duplicate and votes:
            votes=[{**votes[0],"support":True}]*len(votes)
        return ProviderResponse({"reviews":votes,"ranking":[p["id"] for p in payload["proposals"]]},20,20)


def test_every_specialists_objections_reach_next_round_and_revision_can_agree():
    peers={role:ScriptedPeer(role,improve=True) for role in ("mechanics","fabrication","measurement")}
    result=decide(evidence_context(max_rounds=2),provider=peers)
    assert result["status"]=="agreed"
    assert result["rounds"]==2
    for peer in peers.values():
        second_proposal=[p for p in peer.calls if "proposals" not in p][1]
        assert {v["agent"] for v in second_proposal["peer_feedback"]}==set(peers)
    assert len(result["selected"]["supporters"])==3


def test_disagreement_stops_at_round_limit_and_preserves_objections():
    peers={r:ScriptedPeer(r) for r in ("mechanics","fabrication","measurement")}
    result=decide(evidence_context(max_rounds=2),provider=peers)
    assert result["selected"] is None
    assert result["status"]=="no_consensus"
    assert result["rounds"]==2
    assert len(result["reviews"])==18
    assert all(len(p.calls)==4 for p in peers.values())


@pytest.mark.parametrize("fault",["duplicate","unknown","malformed"])
def test_faulty_peer_cannot_manufacture_quorum(fault):
    peers={r:ScriptedPeer(r) for r in ("mechanics","fabrication","measurement")}
    peers["mechanics"]=ScriptedPeer("mechanics",**{fault:True})
    result=decide(evidence_context(max_rounds=1),provider=peers)
    assert result["selected"] is None


def test_abstentions_leave_no_implicit_human_override():
    peers={r:ScriptedPeer(r,abstain=True) for r in ("mechanics","fabrication","measurement")}
    result=decide(evidence_context(max_rounds=1),provider=peers)
    assert result["status"]=="no_valid_proposals"
    assert len(result["transcript"])>=3
    assert result["selected"] is None


def test_provider_timeout_stops_without_retry_and_reserves_unknown_usage():
    class Timeout:
        name="timeout"
        model="local"
        count=0
        def complete_json(self,*args,**kwargs):
            self.count+=1
            raise ProviderError("Timed out")
    provider=Timeout()
    result=decide(evidence_context(),provider=provider)
    # The three proposals of a stage go out together; none is retried and no later stage runs.
    assert 1<=provider.count<=3
    assert not any(t.get("stage")=="review" for t in result["transcript"])
    assert result["status"]=="provider_error"
    assert result["usage"]["reserved_tokens"]>0
    assert result["selected"] is None


def test_design_proposal_cannot_vote_for_a_weaker_test_than_execution():
    context=evidence_context()
    context["model"]["fit"]["status"]="identifiable"
    proposal=Proposal(action="design",candidate_id="candidate",geometry=Geometry(base_width_m=.02),
                      force_n=.15,load_position_m=.06,reason="Weaker load",evidence_ids=("obs",),cost={"cycles":1,"designs":1})
    assert any("proof condition" in e for e in validate_proposal(proposal,context))


def test_design_family_is_frozen_for_a_calibrated_study():
    context=evidence_context()
    context["model"]["fit"]["status"]="identifiable"
    proposal=Proposal(action="design",candidate_id="candidate",geometry=Geometry(section_family="solid_bar"),
                      force_n=.3,load_position_m=.12,reason="Switch family",evidence_ids=("obs",),cost={"cycles":1,"designs":1})
    assert "New designs must preserve section_family" in validate_proposal(proposal,context)


def test_an_unexpected_provider_exception_still_frees_its_token_reservation():
    """A sibling call waiting on the same meter would otherwise block forever (only a restart freed the part)."""
    import sqlite3
    import pytest
    from coform.agents import _meter, _metered_call

    class Locked:
        name, model = "nvidia", "test"

        def complete_json(self, system, payload, max_output_tokens):
            raise sqlite3.OperationalError("database is locked")

    meter = _meter()
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reserved_tokens": 0, "calls": 0}
    with pytest.raises(sqlite3.OperationalError):
        _metered_call(Locked(), "mechanics", "review", "system", {"x": 1}, usage=usage, token_limit=100000, transcript=[], meter=meter)
    assert meter["inflight"] == 0
