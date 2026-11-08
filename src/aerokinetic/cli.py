import argparse
import json
from pathlib import Path
from . import Atmosphere, Contour, Section, Flow, Grid, Plan, naca0012
from .project import Project
from .config import FARFIELDS


def load_case(path):
    project = Project.load(path)
    from dataclasses import asdict
    return project.section, project.flow, project.grid, asdict(project.run)


def main(argv=None):
    parser = argparse.ArgumentParser(description="3D cumulant LBM for fixed multi-element sections")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gui")
    sub = commands.add_parser("resume")
    sub.add_argument("directory", type=Path)
    sub = commands.add_parser("continue", help="Continue a saved CPU result into a NEW time-segment directory")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--additional-time", type=float, required=True)
    sub.add_argument("--average-after", type=float)
    sub = commands.add_parser("compare")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--reference", default="ohtake_re30000")
    for name in ("plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("config", type=Path)
        sub.add_argument("--farfield", choices=FARFIELDS,
                         help="Explicit boundary override; new exterior FD is research, uniform CPU/noSGS; characteristic_transport_v1 remains rejected for airfoil use")
        if name == "run":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--animate", action="store_true")
    sub = commands.add_parser("animate")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--field", default="omega_z", choices=["omega_z", "speed", "cp", "speed_m_s", "pressure_pa"])
    sub.add_argument("--output", type=Path)
    sub = commands.add_parser("analyze", help="Describe unsteady load statistics and create a review archive")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--output", type=Path)
    sub.add_argument("--average-after", type=float)
    sub = commands.add_parser("verify-refinement", help="Experimental fluid remap verification; not a refined airfoil solver")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--no-plot", action="store_true")
    for name in ("plan-refined", "run-refined"):
        sub = commands.add_parser(name, help="Experimental two-level airfoil with fine stationary walls")
        sub.add_argument("config", type=Path)
        if name == "run-refined":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--animate", action="store_true")
    sub = commands.add_parser("resume-refined")
    sub.add_argument("directory", type=Path)
    sub = commands.add_parser("verify-interface", help="Verify experimental spatial coarse/fine fluid interfaces")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "continue":
        from .continuation import continue_run
        result=continue_run(args.directory,args.output,args.additional_time,
                            average_after=args.average_after,progress=lambda m:print(m,flush=True))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if args.command in ("plan-refined", "run-refined"):
        from .refined_solver import RefinedPlan, RefinedSolver, load_refined_project
        project, refinement = load_refined_project(args.config)
        plan = RefinedPlan.create(project, refinement)
        print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2), flush=True)
        if args.command == "plan-refined":
            return
        solver = RefinedSolver(project, refinement, progress=lambda text: print(text, flush=True))
        def sample(solver, record):
            print(f"t*={record['t_star']:.6g} Cl={record['cl']:.7g} Cd={record['cd']:.7g} Cm={record['cm']:.7g}", flush=True)
        print(json.dumps(solver.run(args.output, callback=sample), ensure_ascii=False, indent=2), flush=True)
        if args.animate:
            from .refined_visualization import animate
            print(animate(args.output))
        return
    if args.command == "resume-refined":
        from .refined_solver import RefinedSolver
        print(json.dumps(RefinedSolver.resume_run(args.directory), ensure_ascii=False, indent=2), flush=True)
        return
    if args.command == "gui":
        from .gui import main as gui_main
        gui_main([])
        return
    if args.command == "compare":
        from .experiments import compare_run
        from ._io import write_json
        result = compare_run(args.directory, args.reference)
        path = args.directory / f"experiment_{args.reference}.json"
        write_json(path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "resume":
        from .solver import Solver
        print(json.dumps(Solver.resume_run(args.directory, progress=lambda m: print(m, flush=True)), indent=2))
        return
    if args.command == "animate":
        from .animation import animate
        print(animate(args.directory, args.output, args.field))
        return
    if args.command == "analyze":
        from .temporal import write_analysis
        write_analysis(args.directory, args.output, args.average_after)
        return
    if args.command == "verify-refinement":
        from .refinement_checks import write_checks
        result = write_checks(args.output, plot=not args.no_plot)
        if result["status"] != "passed":
            raise SystemExit(1)
        return
    if args.command == "verify-interface":
        from .interface_checks import write_checks
        result = write_checks(args.output, plot=not args.no_plot)
        if result["status"] != "passed":
            raise SystemExit(1)
        return
    section, flow, grid, options = load_case(args.config)
    if args.farfield is not None:
        from dataclasses import replace
        grid = replace(grid,farfield=args.farfield)
    plan = Plan.create(section, flow, grid)
    if args.command == "plan":
        print(json.dumps(plan.as_dict(), indent=2))
        return
    from .solver import Solver
    print(json.dumps(plan.as_dict(), indent=2), flush=True)
    solver = Solver(section, flow, grid, progress=lambda message: print(message, flush=True))

    def progress(solver, record):
        print(f"t*={record['t_star']:.5f} Cl={record['cl']:.6g} Cd={record['cd']:.6g} Cm={record['cm']:.6g}", flush=True)
    result = solver.run(output=args.output, callback=progress, **options)
    print(json.dumps(result, indent=2))
    if args.animate:
        from .animation import animate
        print(animate(args.output))


if __name__ == "__main__":
    main()
