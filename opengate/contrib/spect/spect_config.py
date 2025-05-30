import opengate as gate
from opengate.contrib.spect.spect_helpers import *
from opengate.actors.biasingactors import distance_dependent_angle_tolerance
from opengate.exception import fatal
from opengate import Simulation, g4_units
from box import Box
import opengate.contrib.spect.ge_discovery_nm670 as nm670
import opengate.contrib.spect.siemens_intevo as intevo
from opengate.image import read_image_info
from opengate.utility import get_basename_and_extension
from opengate.sources.utility import set_source_energy_spectrum
import numpy as np
import matplotlib.pyplot as plt


## FIXME -> separate classes for analog/normal/FF etc ?


class SPECTConfig:
    """
    Represents the configuration object for a SPECT simulation.

    This class organizes and manages the configuration details required to set up
    and execute a SPECT (Single Photon Emission Computed Tomography) simulation.

    It includes settings for the simulation output, main configurable elements,
    such as detector, phantom, source, and protocol, as well as simulation
    initialization and execution methods.
    """

    def __init__(self, simu_name="spect"):
        # default
        self.output_folder = "output"
        self.output_basename = "projection.mhd"
        self.simu_name = simu_name

        # main elements
        self.detector_config = DetectorConfig(self)
        self.phantom_config = PhantomConfig(self)
        self.source_config = SourceConfig(self)
        self.acquisition_config = AcquisitionConfig(self)

    def __str__(self):
        s = f"SPECT simulation\n"
        s += f"Output folder: {self.output_folder}\n"
        s += f"Output basename: {self.output_basename}\n"
        s += f"{self.detector_config}\n"
        s += f"{self.phantom_config}\n"
        s += f"{self.source_config}\n"
        s += f"{self.acquisition_config}\n"
        return s

    def create_simulation(self, sim, number_of_threads=1, visu=False):
        output = Box()
        self.initialize_simulation(sim, output, number_of_threads, visu)
        # init all elements
        self.detector_config.create_simulation(sim, output)
        self.phantom_config.create_simulation(sim, output)
        self.source_config.create_simulation(sim, output)
        self.acquisition_config.create_simulation(sim, output, output.detectors)
        return output

    def initialize_simulation(self, sim, output, number_of_threads, visu):
        # main options
        sim.random_seed = "auto"
        sim.check_volumes_overlap = True
        sim.visu = visu
        sim.visu_type = "qt"
        sim.output_dir = self.output_folder
        sim.progress_bar = True
        sim.store_json_archive = True
        sim.store_input_files = False
        sim.json_archive_filename = f"{self.simu_name}.json"

        # thread
        if visu:
            sim.number_of_threads = 1
        else:
            sim.number_of_threads = number_of_threads

        # world
        m = gate.g4_units.m
        sim.world.size = [3 * m, 3 * m, 3 * m]
        sim.world.material = "G4_AIR"

        # physics
        sim.physics_manager.physics_list_name = "G4EmStandardPhysics_option3"
        sim.physics_manager.global_production_cuts.all = 2 * gate.g4_units.mm

        # add the "stats" actor
        stats = sim.add_actor("SimulationStatisticsActor", "stats")
        stats.output_filename = "stats.txt"

        # store some created elements
        output.stats = stats


class PhantomConfig:
    """
    This class is used in SPECTConfig. Represents a phantom configuration

    This class is used to configure and handle a phantom in a medical imaging
    simulation context. It includes properties for the phantom image,
    labels, density tolerance, and provides methods for printing the
    phantom configuration and generating a simulation. The phantom's
    construction and materials are based on Hounsfield Unit (HU) mappings
    to materials defined in external files.

    Attributes:
        spect_config (SpectConfig): The SpectConfig object containing simulation
            setup and parameters.
        image (Optional[any]): The phantom image data used in the simulation,
            initially set to None.
        labels (Optional[any]): Labels associated with the phantom,
            initially set to None.
        density_tol (float): The density tolerance threshold in g/cm^3
            for material mapping, default set to 0.05 * g/cm^3.

    Methods:
        print(str_only: bool = False) -> Optional[str]:
            Prints or returns a string representation of the phantom configuration.
        create_simulation(visu: Optional[any]):
            Creates a voxelized simulation configuration based on the set
            phantom image and materials.
    """

    def __init__(self, spect_config):
        gcm3 = gate.g4_units.g_cm3
        self.spect_config = spect_config
        # user param
        self.image = None
        self.labels = None
        self.material_db = None
        self.density_tol = 0.05 * gcm3

    def __str__(self):
        s = f"Phantom image: {self.image}\n"
        s += f"Phantom labels: {self.labels}"
        return s

    def create_simulation(self, sim, output):
        # can be: nothing or voxelized
        if self.image is None:
            return
        # special case for visu
        if sim.visu is True:
            self.add_fake_phantom_for_visu(sim, output)
            return
        # insert voxelized phantom
        phantom = sim.add_volume("Image", f"{self.spect_config.simu_name}_phantom")
        phantom.image = self.image
        phantom.material = "G4_AIR"
        if self.material_db is not None:
            sim.volume_manager.add_material_database(self.material_db)
        if self.labels is not None:
            phantom.read_label_to_material(self.labels)
        else:
            f1 = gate.utility.get_data_folder() / "Schneider2000MaterialsTable.txt"
            f2 = gate.utility.get_data_folder() / "Schneider2000DensitiesTable.txt"
            vm, materials = gate.geometry.materials.HounsfieldUnit_to_material(
                sim, self.density_tol, f1, f2
            )
            phantom.voxel_materials = vm
        output.phantom = phantom

    def add_fake_phantom_for_visu(self, sim, output):
        gate.exception.warning(f"FAKE voxelized phantom for visu: {self.image}")
        img_info = read_image_info(self.image)
        phantom = sim.add_volume("Box", f"{self.spect_config.simu_name}_phantom")
        phantom.material = "G4_WATER"
        phantom.size = img_info.size * img_info.spacing
        output.phantom = phantom


class DetectorConfig:
    """
    Used in SPECTConfig. Represents the configuration for a SPECT detector.

    Manage: detector models, crystals, collimators, digitizers,
    and the number of detector heads.
    """

    def __init__(self, spect_config):
        self.spect_config = spect_config
        # spect models
        self.available_models = ("intevo", "nm670")
        # user param
        self.model = None
        self.collimator = None
        self.digitizer_function = None
        self.number_of_heads = 2
        self.garf = GARFConfig(spect_config)

    def __str__(self):
        s = f"Detector model: {self.model}\n"
        s += f"Detector collimator: {self.collimator}\n"
        s += f"Detector digitizer: {self.digitizer_function}\n"
        s += f"Detector # of heads: {self.number_of_heads}"
        return s

    def get_model_module(self):
        m = None
        if self.model == "nm670":
            m = nm670
        if self.model == "intevo":
            m = intevo
        # FIXME veriton
        return m

    def get_detector_normal(self):
        if self.model == "nm670":
            return [0, 0, -1]
        if self.model == "intevo":
            return [1, 0, 0]
        fatal(f"Unknown detector model: {self.model}")

    def get_proj_filename(self, i):
        filename, ext = get_basename_and_extension(self.spect_config.output_basename)
        f = f"{filename}_{i}{ext}"
        return f

    def create_simulation(self, sim, output):
        if self.model not in self.available_models:
            fatal(
                f'The model "{self.model}" is unknown. '
                f"Known models are: {self.available_models}"
            )

        # GARF ?
        if self.garf.is_enabled():
            self.garf.create_simulation(sim, output)
            return

        # or real SPECT volumes
        output.detectors = []
        output.crystals = []
        output.digitizers = []
        m = self.get_model_module()
        simu_name = self.spect_config.simu_name
        for i in range(self.number_of_heads):
            det, colli, crystal = m.add_spect_head(
                sim,
                f"{simu_name}_spect{i}",
                collimator_type=self.collimator,
                debug=sim.visu == True,
            )
            output.detectors.append(det)
            output.crystals.append(crystal)
            # set the digitizer
            if self.digitizer_function is not None:
                self.digitizer_function(sim, crystal.name, f"{simu_name}_digit{i}")
                projs = sim.actor_manager.find_actors(
                    f"{simu_name}_digit{i}_projection"
                )
                if len(projs) != 1:
                    fatal(
                        f"Cannot find the projection actor "
                        f'"{simu_name}_digit{i}_projection"'
                        f"found : {projs}"
                    )
                proj = projs[0]
                proj.output_filename = self.get_proj_filename(i)
                output.digitizers.append(proj)


class GARFConfig:
    """
    Used in DetectorConfig.
    Configuration class for GARF detector.
    """

    def __init__(self, spect_config):
        self.spect_config = spect_config
        # options
        mm = gate.g4_units.mm
        self.spacing = [
            4.7951998710632 * mm / 2,
            4.7951998710632 * mm / 2,
        ]  # FIXME intevo
        self.size = [128 * 2, 128 * 2]
        self.pth_filename = None
        self.batch_size = 1e5
        self.verbose_batch = True
        self.gpu_mode = "auto"
        # built objects
        self.detectors = []
        self.garf_actors = []

    def __str__(self):
        s = f"GARF : {self.pth_filename}\n"
        s += f"GARF image size: {self.size}\n"
        s += f"GARF image spacing: {self.spacing}\n"
        s += f"GARF batch size: {self.batch_size}"
        return s

    def is_enabled(self):
        return self.pth_filename is not None

    def create_simulation(self, sim, output):
        fatal("TODO")
        m = self.spect_config.get_model_module()
        colli = self.spect_config.detector_config.collimator
        for i in range(self.spect_config.detector_config.number_of_heads):
            # create the plane
            det_plane = m.add_detection_plane_for_arf(
                sim, f"{self.spect_config.simu_name}_det{i}", colli
            )
            self.spect_config.detector_config.detectors.append(det_plane)

            # set the position in front of the collimator
            _, crystal_distance, _ = m.compute_plane_position_and_distance_to_crystal(
                colli
            )
            # rotate_gantry(det_plane, radius=0, start_angle_deg=0) # FIXME later

            # output filename
            arf = sim.add_actor(
                "ARFActor", f"{self.spect_config.simu_name}_{det_plane.name}_arf"
            )
            arf.attached_to = det_plane.name
            arf.output_filename = self.spect_config.detector_config.get_proj_filename(i)
            arf.batch_size = 1e5
            arf.image_size = self.size
            arf.image_spacing = self.spacing
            arf.verbose_batch = False
            arf.distance_to_crystal = crystal_distance  # 74.625 * mm
            arf.pth_filename = self.pth_filename
            arf.gpu_mode = "auto"

            # specific to each SPECT model
            if self.spect_config.detector_config.model == "intevo":
                arf.flip_plane = False
                arf.plane_axis = [1, 2, 0]
            if self.spect_config.detector_config.model == "nm670":
                fatal(f"TODO")


class SourceConfig:
    """
    Used in SPECTConfig. Represents a configuration for an activity source.

    This class is used to define and manage the settings of an activity source,
    such as the source image, radionuclide, and total activity.
    """

    def __init__(self, spect_config):
        self.spect_config = spect_config
        # user param
        self.image = None
        self.radionuclide = None
        self.total_activity = 1 * g4_units.Bq
        # self.gaga = GAGAConfig(spect_config) # FIXME todo

    def __str__(self):
        s = f"Activity source image: {self.image}\n"
        s += f"Activity source radionuclide: {self.radionuclide}\n"
        s += f"Activity source: {self.total_activity/gate.g4_units.Bq} Bq"
        return s

    def create_simulation(self, sim, output):
        # can be: nothing or voxelized or gaga (later)
        if self.image is None:
            return
        if self.radionuclide is None:
            fatal(f"Radionuclide is None, please set a radionuclide (eg. 'lu177')")

        # FIXME gaga tests
        source = sim.add_source("VoxelSource", f"{self.spect_config.simu_name}_source")
        source.image = self.image
        set_source_energy_spectrum(source, self.radionuclide)
        source.particle = "gamma"
        source.activity = self.total_activity
        if sim.visu is True:
            source.activity = 100 * gate.g4_units.Bq

        output.source = source


class AcquisitionConfig:
    """
    Used in SPECTConfig. A configuration class for acquisition settings specific to a simulation.

    This class provides configuration parameters and functionality for managing
    acquisition settings in SPECT simulations. It allows specifying parameters
    such as acquisition duration, radius, and number of angles.
    """

    def __init__(self, spect_config):
        self.spect_config = spect_config
        # user param
        self.duration = 1 * gate.g4_units.s
        self.radius = None
        # self.angles = None
        self.number_of_angles = 1
        # internal var
        self.available_starting_head_angles = {
            "1": [0.0],
            "2": [0.0, 180.0],
            "3": [0.0, 120.0, 240.0],
            "4": [0.0, 90.0, 180.0, 270.0],
            # FIXME later for Veriton
        }

    def __str__(self):
        s = f"Acquisition duration: {self.duration/g4_units.s} sec\n"
        s += f"Acquisition radius: {self.radius/g4_units.mm} mm\n"
        s += f"Acquisition # angles: {self.number_of_angles}"
        return s

    def create_simulation(self, sim, output, detectors):
        # get the number of heads and starting angles
        nb = str(self.spect_config.detector_config.number_of_heads)
        if nb not in self.available_starting_head_angles:
            fatal(f"The number of heads must in {self.available_starting_head_angles}")
        starting_head_angles = self.available_starting_head_angles[nb]

        # set the rotation angles (runs)
        step_time = self.duration / self.number_of_angles
        sim.run_timing_intervals = [
            [i * step_time, (i + 1) * step_time] for i in range(self.number_of_angles)
        ]

        # compute the gantry rotations
        step_angle = 360.0 / len(starting_head_angles) / self.number_of_angles
        m = self.spect_config.detector_config.get_model_module()
        i = 0
        for sa in starting_head_angles:
            m.rotate_gantry(
                detectors[i], self.radius, sa, step_angle, self.number_of_angles
            )
            i += 1


def spect_freeflight_run(sc, options, nb_threads=1, visu=False):
    """
    This is a help function to run primary + scatter simulations.
    Only works if the source is unique and managed by the SPECTConfig.
    """

    # prepare an output dict
    output = Box()
    output_folder = sc.output_folder.resolve()
    output.options = options

    # run 1 = primary
    print("----------------------------------------")
    print("Simulation 1/2: primary")
    sim = gate.Simulation()
    output_prim = sc.create_simulation(sim, number_of_threads=nb_threads, visu=visu)
    sim.output_dir = sim.output_dir / "primary"
    options.volume_names = [c.name for c in output_prim.crystals]
    spect_freeflight_initialize_primary(sim, sc, output_prim.source, options)
    sim.run(start_new_process=True)

    # store some output information
    output.nb_detectors = len(output_prim.detectors)
    output.prim = Box()
    output.prim.stats = output_prim.stats.stats.merged_data
    output.prim.activity = (
        output_prim.source.activity / g4_units.Bq * sim.number_of_threads
    )
    output.prim.proj_paths = [
        str(d.get_output_path("counts").relative_to(output_folder))
        for d in output_prim.digitizers
    ]
    output.prim.squared_proj_paths = [
        str(d.get_output_path("squared_counts").relative_to(output_folder))
        for d in output_prim.digitizers
    ]
    print(output_prim.stats)

    # run 2 = scatter
    print("----------------------------------------")
    print("Simulation 2/2: scatter")
    sim = gate.Simulation()
    output_scatter = sc.create_simulation(sim, number_of_threads=nb_threads, visu=visu)
    sim.output_dir = sim.output_dir / "scatter"
    options.volume_names = [c.name for c in output_scatter.crystals]
    ff = spect_freeflight_initialize_scatter(sim, sc, output_scatter.source, options)
    output_scatter.source.activity = options.scatter_activity
    sim.run(start_new_process=True)

    # store some output information
    output.scatter = Box()
    output.scatter.stats = output_scatter.stats.stats.merged_data
    output.scatter.activity = (
        output_scatter.source.activity / g4_units.Bq * sim.number_of_threads
    )
    output.scatter.proj_paths = [
        str(d.get_output_path("counts").relative_to(output_folder))
        for d in output_scatter.digitizers
    ]
    output.scatter.squared_proj_paths = [
        str(d.get_output_path("squared_counts").relative_to(output_folder))
        for d in output_scatter.digitizers
    ]
    print(output_scatter.stats)
    print(ff)

    return output


def spect_freeflight_initialize(sim, sc):
    # Weights MUST be in the digitizer
    hits_actors = sim.actor_manager.find_actors("hits")
    if len(hits_actors) == 0:
        fatal(
            f'Cannot find actors with name "hits" in the simulation. '
            f"{sim.actor_manager.actors}"
        )
    for ha in hits_actors:
        if "Weight" not in ha.attributes:
            ha.attributes.append("Weight")

    # be sure the squared counts flag is enabled
    proj_actors = sim.actor_manager.find_actors("projection")
    if len(proj_actors) == 0:
        fatal(
            f"Cannot find the digitizers in the detector config. {sc.detector_config}"
        )
    for d in proj_actors:
        d.squared_counts.active = True

    # GeneralProcess must *NOT* be used
    s = f"/process/em/UseGeneralProcess false"
    if s not in sim.g4_commands_before_init:
        sim.g4_commands_before_init.append(s)


def spect_freeflight_initialize_primary(sim, sc, source, options):
    spect_freeflight_initialize(sim, sc)
    options = Box(options)

    if "volume_names" not in options:
        crystals = sim.volume_manager.find_volumes("crystal")
        crystal_names = [c.name for c in crystals]
        options.volume_names = crystal_names

    # add the ff actor (only once !)
    normal_vector = sc.detector_config.get_detector_normal()
    ff_name = f"{sc.simu_name}_ff"
    if ff_name not in sim.actor_manager.actors:
        ff = sim.add_actor("GammaFreeFlightActor", ff_name)
        ff.attached_to = "world"
        ff.ignored_volumes = options.volume_names
        ff.minimal_weight = 1e-100
    else:
        ff = sim.actor_manager.get_actor(ff_name)

    if "forced_direction_flag" in options:
        if options.forced_direction_flag:
            _spect_freeflight_initialize_primary_fd(sim, sc, source, options)
            return ff

    source.activity = options.primary_activity
    source.direction.acceptance_angle.skip_policy = "SkipEvents"
    if "max_rejection" in options:
        source.direction.acceptance_angle.max_rejection = options.max_rejection
    source.direction.acceptance_angle.volumes = options.volume_names
    source.direction.acceptance_angle.forced_direction_flag = False
    source.direction.acceptance_angle.intersection_flag = True
    source.direction.acceptance_angle.normal_flag = True
    source.direction.acceptance_angle.normal_vector = normal_vector
    source.direction.acceptance_angle.normal_tolerance = options.angle_tolerance
    # distance dependent normal tol is very slow, dont use it
    source.direction.acceptance_angle.distance_dependent_normal_tolerance = False
    # minimal distance should not be used for primary
    source.direction.acceptance_angle.normal_tolerance_min_distance = 0

    return ff


def _spect_freeflight_initialize_primary_fd(sim, sc, source, options):
    options = Box(options)

    # need an additional source copy for each head
    sources = [source]
    for i in range(1, sc.detector_config.number_of_heads):
        s = sim.source_manager.add_source_copy(source.name, f"{source.name}_{i}")
        sources.append(s)

    normal_vector = sc.detector_config.get_detector_normal()
    i = 0
    for source in sources:
        source.activity = options.primary_activity
        print(source.activity)
        source.direction.acceptance_angle.skip_policy = "SkipEvents"
        source.direction.acceptance_angle.volumes = [options.volume_names[i]]
        source.direction.acceptance_angle.normal_vector = normal_vector
        source.direction.acceptance_angle.normal_tolerance = options.angle_tolerance
        source.direction.acceptance_angle.normal_flag = False
        source.direction.acceptance_angle.intersection_flag = False
        source.direction.acceptance_angle.forced_direction_flag = True
        i += 1


def spect_freeflight_initialize_primary_OLD_TO_REMOVE(sim, sc, options, sources=None):
    spect_freeflight_initialize(sim, sc)
    options = Box(options)

    # consider the sources to bias
    if sources is None:
        sources = [sc.source_config.source]
    # check if sources is a list or a single element
    if not isinstance(sources, list):
        sources = [sources]

    # get some information from spect config
    crystal_names = [c.name for c in sc.detector_config.crystals]
    total_activity = sum([source.activity for source in sources])

    # add the ff actor (only one !)
    n = sc.detector_config.get_detector_normal()
    ff_name = f"{sc.simu_name}_ff"
    if ff_name not in sim.actor_manager.actors:
        ff = sim.add_actor("GammaFreeFlightActor", ff_name)
        ff.attached_to = "world"
        ff.ignored_volumes = crystal_names
    else:
        ff = sim.actor_manager.get_actor(ff_name)

    for source in sources:
        if total_activity != 0:
            source.activity = (
                source.activity / total_activity
            ) * options.primary_activity
        else:
            source.activity = options.primary_activity
        source.direction.acceptance_angle.skip_policy = "SkipEvents"
        source.direction.acceptance_angle.max_rejection = 1000
        source.direction.acceptance_angle.volumes = crystal_names
        source.direction.acceptance_angle.intersection_flag = True
        source.direction.acceptance_angle.normal_flag = True
        source.direction.acceptance_angle.normal_vector = n
        source.direction.acceptance_angle.normal_tolerance = options.angle_tolerance
        # distance dependent normal tol is very slow, dont use it
        source.direction.acceptance_angle.distance_dependent_normal_tolerance = False
        # minimal distance should not be used for primary
        source.direction.acceptance_angle.normal_tolerance_min_distance = 0
        # Two methods: AA or FD
        # AA = Angular Acceptance rejection
        # FD = Forced Direction
        if "forced_direction_flag" in options:
            if options.forced_direction_flag:
                source.direction.acceptance_angle.normal_flag = False
                source.direction.acceptance_angle.intersection_flag = False
                source.direction.acceptance_angle.forced_direction_flag = True

    return ff


def spect_freeflight_initialize_scatter(sim, sc, source, options):
    spect_freeflight_initialize(sim, sc)
    options = Box(options)

    if "volume_names" not in options:
        crystals = sim.volume_manager.find_volumes("crystal")
        crystal_names = [c.name for c in crystals]
        options.volume_names = crystal_names

    # reset the source biasing if it was changed by "primary"
    source.direction.acceptance_angle.intersection_flag = False
    source.direction.acceptance_angle.normal_flag = False
    source.direction.acceptance_angle.forced_direction_flag = False
    source.activity = options.scatter_activity

    # set the FF actor for scatter
    n = sc.detector_config.get_detector_normal()
    ff = sim.add_actor("ScatterSplittingFreeFlightActor", f"{sc.simu_name}_ff")
    ff.attached_to = "world"
    ff.minimal_weight = 1e-100
    ff.ignored_volumes = options.volume_names
    ff.compton_splitting_factor = options.compton_splitting_factor
    ff.rayleigh_splitting_factor = options.rayleigh_splitting_factor
    ff.max_compton_level = options.max_compton_level
    ff.acceptance_angle.intersection_flag = True
    ff.acceptance_angle.normal_flag = True
    ff.acceptance_angle.forced_direction_flag = False
    ff.acceptance_angle.volumes = options.volume_names
    ff.acceptance_angle.normal_vector = n
    ff.acceptance_angle.normal_tolerance = options.angle_tolerance
    if "angle_tolerance_min_distance" in options:
        ff.acceptance_angle.normal_tolerance_min_distance = (
            options.angle_tolerance_min_distance
        )
    ff.acceptance_angle.distance_dependent_normal_tolerance = False

    """ REMOVED because too slow
    ff.acceptance_angle.distance_dependent_normal_tolerance = True
    tol = options.scatter_angle_tolerance
    ff.acceptance_angle.distance1 = tol[0]
    ff.acceptance_angle.angle1 = tol[1]
    ff.acceptance_angle.distance2 = tol[2]
    ff.acceptance_angle.angle2 = tol[3]
    """
    print(ff.user_info)
    return ff


def spect_freeflight_initialize_scatter_OLD_TO_REMOVE(sim, sc, options, sources=None):
    spect_freeflight_initialize(sim, sc)
    options = Box(options)

    # get some information from spect config
    crystal_names = [c.name for c in sc.detector_config.crystals]

    # reset the source biasing if it was changed by "primary"
    # set the activity
    if sources is None:
        sources = [sc.source_config.source]
    total_activity = sum([source.activity for source in sources])
    for source in sources:
        source.direction.acceptance_angle.intersection_flag = False
        source.direction.acceptance_angle.normal_flag = False
        if total_activity > 0:
            source.activity = (
                source.activity / total_activity
            ) * options.scatter_activity
        else:
            source.activity = options.scatter_activity

    # run 1 = primary
    n = sc.detector_config.get_detector_normal()
    ff = sim.add_actor("ScatterSplittingFreeFlightActor", f"{sc.simu_name}_ff")
    ff.attached_to = "world"
    ff.ignored_volumes = crystal_names
    ff.compton_splitting_factor = options.compton_splitting_factor
    ff.rayleigh_splitting_factor = options.rayleigh_splitting_factor
    ff.max_compton_level = options.max_compton_level
    ff.acceptance_angle.intersection_flag = True
    ff.acceptance_angle.normal_flag = True
    ff.acceptance_angle.volumes = crystal_names
    ff.acceptance_angle.normal_vector = n
    ff.acceptance_angle.normal_tolerance = options.angle_tolerance
    if "angle_tolerance_min_distance" in options:
        ff.acceptance_angle.normal_tolerance_min_distance = (
            options.angle_tolerance_min_distance
        )
    ff.acceptance_angle.distance_dependent_normal_tolerance = False

    """ REMOVED because too slow
    ff.acceptance_angle.distance_dependent_normal_tolerance = True
    tol = options.scatter_angle_tolerance
    ff.acceptance_angle.distance1 = tol[0]
    ff.acceptance_angle.angle1 = tol[1]
    ff.acceptance_angle.distance2 = tol[2]
    ff.acceptance_angle.angle2 = tol[3]
    """

    return ff


def spect_freeflight_merge(output_data, output_folder, n_target):
    output_data = Box(output_data)
    nd = output_data.nb_detectors
    n_prim = output_data.prim.activity
    n_scatter = output_data.scatter.activity

    for i in range(nd):
        # primary
        img = output_folder / output_data["prim"]["proj_paths"][i]
        sq_img = output_folder / output_data["prim"]["squared_proj_paths"][i]
        out = img.parent / f"relative_uncertainty_primary_{i}.mhd"
        print(img, out)
        _, prim, prim_squared = history_rel_uncertainty_from_files(
            img, sq_img, n_prim, out
        )
        # scatter
        img = output_folder / output_data["scatter"]["proj_paths"][i]
        sq_img = output_folder / output_data["scatter"]["squared_proj_paths"][i]
        out = img.parent / f"relative_uncertainty_scatter_{i}.mhd"
        print(img, out)
        _, scatter, scatter_squared = history_rel_uncertainty_from_files(
            img, sq_img, n_scatter, out
        )
        # combined
        uncert, mean = history_ff_combined_rel_uncertainty(
            prim, prim_squared, scatter, scatter_squared, n_prim, n_scatter
        )
        scaling = n_target / n_prim
        mean = mean * scaling
        print(f"Primary n = {n_prim}  Scatter n = {n_scatter}  Target n = {n_target}")
        print(f"Primary to scatter ratio = {n_prim / n_scatter:.01f}")
        print(f"Scaling to target        = {scaling:.01f}")

        # write combined image
        prim_img = sitk.ReadImage(img)
        img = sitk.GetImageFromArray(mean)
        img.CopyInformation(prim_img)
        fn = output_folder / f"mean_{i}.mhd"
        sitk.WriteImage(img, fn)
        print(fn)

        # write combined relative uncertainty
        img = sitk.GetImageFromArray(uncert)
        img.CopyInformation(prim_img)
        fn = output_folder / f"relative_uncertainty_{i}.mhd"
        sitk.WriteImage(img, fn)
        print(fn)


def plot_ddaa(acceptance_angle, output_filename=None):
    # plot dd
    a1 = acceptance_angle.angle1
    a2 = acceptance_angle.angle2
    d1 = acceptance_angle.distance1
    d2 = acceptance_angle.distance2
    distances = np.linspace(d1 / 2, d2 * 2, 200)
    angles = [distance_dependent_angle_tolerance(a1, a2, d1, d2, d) for d in distances]

    cm = g4_units.cm
    plt.figure(figsize=(8, 6))
    plt.plot(distances / cm, np.degrees(angles), label="Distance vs Angle")
    plt.xlabel("Distance (cm)")
    plt.ylabel("Angle (degrees)")
    plt.title("Distance vs Angle Tolerance")
    plt.grid()
    plt.legend()
    # plt.show()
    if output_filename is not None:
        plt.savefig(output_filename)
    return plt
