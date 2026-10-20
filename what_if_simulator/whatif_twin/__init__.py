# CardioTwin what-if simulator: a personalised, explainable "digital twin" sandbox built on the saved notebook 05 models,
# the notebook 06 SHAP method and the notebook 07 risk engine. Nothing is retrained. Every output is a hypothetical
# model simulation: it shows how the model's output responds to edited inputs, not the effect of any treatment.
#
#   from whatif_twin import load_simulator, Scenario
#   sim = load_simulator()                       # finds the project artifacts, loads both models once
#   case = sim.patients.get("stay-37093652")     # or sim.patients.synthetic_demo()
#   res = sim.simulate(case, Scenario.of("HR 10 lower", heart_rate={"delta": -10}))
from .scenario import Change, Scenario  # noqa: F401
from .simulator import WhatIfSimulator, load_simulator  # noqa: F401

__version__ = "1.0.0"
LABEL = ("HYPOTHETICAL MODEL SIMULATION. It shows how this model's output responds to edited inputs. It does not change "
         "the patient's record, does not predict the effect of any treatment and is not medical advice.")
