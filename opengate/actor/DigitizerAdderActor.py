import opengate as gate
import opengate_core as g4


class DigitizerAdderActor(g4.GateDigitizerAdderActor, gate.ActorBase):
    """
    Equivalent to Gate "adder": gather all hits of an event in the same volume.
    Input: a HitsCollection, need aat least TotalEnergyDeposit and PostPosition attributes
    Output: a Single collections

    Policies:
    - TakeEnergyWinner: consider position and energy of the hit with the max energy
       -> all other attributes (Time, etc): the value of the winner is used.
    - TakeEnergyCentroid: computed the energy-weighted centroid
       -> all other attributes (Time, etc): the value the last seen hit is used.

    """

    type_name = "DigitizerAdderActor"

    @staticmethod
    def set_default_user_info(user_info):
        gate.ActorBase.set_default_user_info(user_info)
        user_info.attributes = []
        user_info.output = "singles.root"
        user_info.input_digi_collection = "Hits"
        user_info.policy = "TakeEnergyWinner"
        user_info.skip_attributes = []
        user_info.clear_every = 1e5
        user_info.group_volume = None

    def __init__(self, user_info):
        gate.ActorBase.__init__(self, user_info)
        g4.GateDigitizerAdderActor.__init__(self, user_info.__dict__)
        actions = {"StartSimulationAction", "EndSimulationAction"}
        self.AddActions(actions)

    def __del__(self):
        pass

    def __str__(self):
        s = f"DigitizerAdderActor {self.user_info.name}"
        return s

    def set_group_by_depth(self):
        depth = -1
        if self.user_info.group_volume is not None:
            sim = self.simulation
            depth = sim.volume_manager.get_volume_depth(self.user_info.group_volume)
            print(f"volume depth {self.user_info.group_volume} : {depth}")
        self.SetGroupVolumeDepth(depth)

    def StartSimulationAction(self):
        self.set_group_by_depth()
        g4.GateDigitizerAdderActor.StartSimulationAction(self)

    def EndSimulationAction(self):
        g4.GateDigitizerAdderActor.EndSimulationAction(self)
