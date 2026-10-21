"""Unit-safe carbon/energy ledger functions.

Two separate ledgers are computed by this module and must never be summed:

- 교통 장부 (transport ledger): `required_avoided_vkm` — how many avoided
  vehicle-km are needed to offset a given extra emission.
- 에너지 장부 (energy ledger): `pv_capacity_kw`, `pv_energy_mwh`,
  `payback_years` — PV shading-canopy capacity, generation, and embodied-carbon
  payback time.

Units, spelled out per argument/return so unit mistakes are caught by
inspection rather than by a wrong number downstream:

- `required_avoided_vkm(extra_emission_t: float, ef_kg_per_vkm: float) -> float`
  - extra_emission_t: t (metric tons of CO2-equivalent)
  - ef_kg_per_vkm: kg/vkm (emission factor per vehicle-kilometre)
  - returns: vkm (vehicle-kilometres)

- `pv_capacity_kw(area_m2: float, density_wp_m2: float) -> float`
  - area_m2: m² (panel/canopy area)
  - density_wp_m2: Wp/m² (rated power density)
  - returns: kW (rated capacity)

- `pv_energy_mwh(p_rated_kw: float, cf: float, hours: float = 8760.0) -> float`
  - p_rated_kw: kW (rated capacity)
  - cf: dimensionless capacity factor in [0, 1]
  - hours: h (period length, default one year = 8760 h)
  - returns: MWh (energy generated over the period)

- `payback_years(embodied_t: float, annual_saving_t: float) -> float`
  - embodied_t: t (embodied carbon of the installation)
  - annual_saving_t: t/year (annual carbon saving)
  - returns: years (simple payback time)

These two ledgers use incompatible units (t of avoided transport emissions
vs. t of avoided energy emissions from a physically separate intervention)
and answer different questions, so their outputs are never added together
into a combined total anywhere in this codebase.
"""


def required_avoided_vkm(extra_emission_t: float, ef_kg_per_vkm: float) -> float:
    """Vehicle-km (vkm) that must be avoided to offset extra_emission_t.

    extra_emission_t: t. ef_kg_per_vkm: kg/vkm, must be > 0. Returns vkm.
    """
    if extra_emission_t < 0:
        raise ValueError(f"extra_emission_t must be >= 0, got {extra_emission_t}")
    if ef_kg_per_vkm <= 0:
        raise ValueError(f"ef_kg_per_vkm must be > 0, got {ef_kg_per_vkm}")
    return extra_emission_t * 1000.0 / ef_kg_per_vkm


def pv_capacity_kw(area_m2: float, density_wp_m2: float) -> float:
    """Rated PV capacity in kW from area (m²) and power density (Wp/m²)."""
    if area_m2 < 0:
        raise ValueError(f"area_m2 must be >= 0, got {area_m2}")
    if density_wp_m2 < 0:
        raise ValueError(f"density_wp_m2 must be >= 0, got {density_wp_m2}")
    return area_m2 * density_wp_m2 / 1000.0


def pv_energy_mwh(p_rated_kw: float, cf: float, hours: float = 8760.0) -> float:
    """Energy generated in MWh from rated capacity (kW), capacity factor, and hours.

    cf must be in [0, 1] (dimensionless). hours must be > 0 (h).
    """
    if p_rated_kw < 0:
        raise ValueError(f"p_rated_kw must be >= 0, got {p_rated_kw}")
    if cf < 0 or cf > 1:
        raise ValueError(f"cf must be in [0, 1], got {cf}")
    if hours <= 0:
        raise ValueError(f"hours must be > 0, got {hours}")
    return p_rated_kw * cf * hours / 1000.0


def payback_years(embodied_t: float, annual_saving_t: float) -> float:
    """Simple payback time in years: embodied carbon (t) / annual saving (t/year).

    annual_saving_t must be > 0.
    """
    if embodied_t < 0:
        raise ValueError(f"embodied_t must be >= 0, got {embodied_t}")
    if annual_saving_t <= 0:
        raise ValueError(f"annual_saving_t must be > 0, got {annual_saving_t}")
    return embodied_t / annual_saving_t
