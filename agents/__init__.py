def make(name, *args):
    from agents.assembly import AssemblyAgent
    from agents.design import DesignAgent
    from agents.fit import FitAgent
    from agents.generalist import GeneralistAgent
    from agents.manufacturing import ManufacturingAgent
    classes = {"design": DesignAgent, "assembly": AssemblyAgent, "manufacturing": ManufacturingAgent,
               "fit": FitAgent, "generalist": GeneralistAgent}
    return classes[name](*args)
