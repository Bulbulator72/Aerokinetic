"""Plan consistent freestream inputs for the proposed standalone application.

Standard library only. This tool never launches CFD and does not extend the
input limits or validated scope of the existing aerokinetic solver.
"""
import argparse
import json
import math
from pathlib import Path


ISA_TEMPERATURE_K = 288.15
ISA_PRESSURE_PA = 101325.0
AIR_GAS_CONSTANT = 287.05287
AIR_GAMMA = 1.4
ISA_DYNAMIC_VISCOSITY = 1.7894e-5


def resolve_case(*, reynolds=None, mach=None, chord_m=None, speed_m_s=None, alpha_deg=0.0):
    supplied = {"reynolds": reynolds, "mach": mach, "chord_m": chord_m, "speed_m_s": speed_m_s}
    for key, value in supplied.items():
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"{key} must be finite and positive")
    if not math.isfinite(alpha_deg) or not -10 <= alpha_deg <= 30:
        raise ValueError("The proposed application targets -10 <= alpha_deg <= 30")
    rho = ISA_PRESSURE_PA / (AIR_GAS_CONSTANT * ISA_TEMPERATURE_K)
    nu = ISA_DYNAMIC_VISCOSITY / rho
    sound_speed = math.sqrt(AIR_GAMMA * AIR_GAS_CONSTANT * ISA_TEMPERATURE_K)
    if chord_m is not None:
        chord = chord_m
        if speed_m_s is not None:
            speed = speed_m_s
        elif mach is not None:
            speed = mach * sound_speed
        elif reynolds is not None:
            speed = reynolds * nu / chord
        else:
            raise ValueError("Provide chord plus Re, Mach or speed")
    elif reynolds is not None:
        if speed_m_s is not None:
            speed = speed_m_s
        elif mach is not None:
            speed = mach * sound_speed
        else:
            raise ValueError("Provide Re plus Mach, speed or chord")
        chord = reynolds * nu / speed
    else:
        raise ValueError("Provide two independent inputs; speed and Mach alone do not determine chord or Re")
    result_values = {"chord_m": chord, "speed_m_s": speed,
                     "reynolds": speed * chord / nu, "mach": speed / sound_speed}
    if not all(math.isfinite(v) and v > 0 for v in result_values.values()):
        raise ValueError("The derived freestream quantities are not finite and positive")
    for key, value in supplied.items():
        if value is not None and not math.isclose(value, result_values[key], rel_tol=1e-5, abs_tol=1e-12):
            raise ValueError(f"Inconsistent {key}: provided {value:g}, implied {result_values[key]:g} for ISA air")
    re_value, mach_value = result_values["reynolds"], result_values["mach"]
    if not 1e4 * (1 - 1e-12) <= re_value <= 1e8 * (1 + 1e-12):
        raise ValueError(f"Derived Re={re_value:g} is outside the proposed range 1e4..1e8")
    if mach_value > 0.4 * (1 + 1e-12):
        raise ValueError(f"Derived Mach={mach_value:g} exceeds 0.4; change the physical case, not its label")
    legacy_range = re_value <= 1e5 * (1 + 1e-12) and mach_value <= 0.15 * (1 + 1e-12)
    return {
        "freestream_inputs_consistent": True,
        **result_values,
        "alpha_deg": alpha_deg,
        "atmosphere": {"temperature_k": ISA_TEMPERATURE_K, "pressure_pa": ISA_PRESSURE_PA,
                       "rho_kg_m3": rho, "nu_m2_s": nu, "sound_speed_m_s": sound_speed},
        "matches_legacy_solver_input_limits": legacy_range,
        "legacy_solver_limits": {"reynolds_max": 1e5, "mach_max": 0.15},
        "solver_executed": False,
        "aerodynamic_validation": "not_performed",
        "note": ("Input planning only. Matching legacy input limits does not establish aerodynamic accuracy. "
                 "Cases outside those limits require a new verified solver; this tool does not enable them.")
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reynolds", type=float)
    parser.add_argument("--mach", type=float)
    parser.add_argument("--chord-m", type=float)
    parser.add_argument("--speed-m-s", type=float)
    parser.add_argument("--alpha-deg", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = resolve_case(reynolds=args.reynolds, mach=args.mach, chord_m=args.chord_m,
                              speed_m_s=args.speed_m_s, alpha_deg=args.alpha_deg)
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(result, indent=2)
    if args.output is not None:
        if args.output.exists():
            parser.error("Output already exists; choose a new filename")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
