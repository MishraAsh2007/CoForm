"""Part-family interface. A family supplies everything part-specific; the board, protocol, agents, PreForm and
fit code stay the same for every family.

A family provides:
  name, experimental        experimental families are labelled so in every output
  key_owner                 top-level spec key -> owning agent (protocol uses it for edit rights / needs)
  check_owners              check name -> owning agent
  default_spec(brief, rules, cfg, start) -> spec
  validate(spec) -> spec    raise ValueError with a readable message
  build(spec, brief, cfg, out_dir) -> {"parts": {name: stl_path}, "meta": {...}, ...}
  checks(spec, brief, cfg, rules, built) -> {check_name: result}   (result: passed, failures[...], metrics)
  quality(spec, ev, rules, cfg, brief, real_preform) -> {"score": ..., components...}
  hint(check, failure, spec, ctx) -> (patch, reason) or None      suggested fix (may be None)
  render(built, out_dir, cfg) -> {kind: png path}
  context(spec, brief) -> dict for agent prompts (component data, description...)
"""


class PartFamily:
    name = "base"
    experimental = False
    key_owner = {}
    check_owners = {}

    def default_spec(self, brief, rules, cfg, start="naive"):
        raise NotImplementedError

    def validate(self, spec):
        return spec

    def build(self, spec, brief, cfg, out_dir):
        raise NotImplementedError

    def checks(self, spec, brief, cfg, rules, built):
        raise NotImplementedError

    def quality(self, spec, ev, rules, cfg, brief, real_preform=None):
        return {"score": 0.0}

    def hint(self, check, failure, spec, ctx):
        return None

    def render(self, built, out_dir, cfg):
        return {}

    def context(self, spec, brief):
        return {}
