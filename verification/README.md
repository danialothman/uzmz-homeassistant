# Formal verification of UZMZ's prepaid pricing

Machine-checked proofs, in [Lean 4](https://lean-lang.org/) with
[Mathlib](https://github.com/leanprover-community/mathlib4), of the maths behind
UZMZ's top-up pricing and balance estimate (`custom_components/uzmz/usage.py`).
They're not part of the integration: HACS only installs `custom_components/`.

## What is proven

All in exact rational numbers (`ℚ`), with no `sorry`. Every theorem depends only
on Lean's standard axioms (`propext`, `Classical.choice`, `Quot.sound`).

**Tariff bands** (`Verification/Tariff.lean`, the model of `tariff_cost`,
`tariff_slices` and `price_top_up`):

| Theorem | Says |
|---|---|
| `elecCost_mono`, `waterCost_mono` | Buying more never costs less. |
| `elecCost_eq_piecewise` | The band-by-band model equals the closed form `tariff_cost` computes. |
| `elec_bands_partition`, `water_bands_partition` | The bands split the units exactly: none lost, none counted twice. |
| `topUp_split` | **Splitting a top-up doesn't change its price.** |
| `month_total` | A month's top-ups cost what their total costs, whatever the amounts. With `topUp_split`, this is why there's no early-month bonus. |
| `elec_topUp_bounds` | Every kWh costs between B$0.01 and B$0.12. |
| `elec_later_costs_more` | A later top-up in the month never buys cheaper units. |

**Price layers, oldest first** (`Verification/Layers.lean`, the model of
`layers_value`, `use_layers` and a top-up's layers):

| Theorem | Says |
|---|---|
| `use_conserves` | Balance before = cost of the usage + balance after. |
| `use_units` | Using `x` units leaves exactly `x` fewer. |
| `use_valid` | No layer ever goes negative. |
| `use_twice` | Estimating hourly costs the same as estimating once. |
| `elecSlices_value`, `elecSlices_units`, `elecSlices_valid` | A top-up adds layers worth exactly what was paid, holding exactly the units bought. |

**Real figures** (`Verification/RealData.lean`), from one household's USMS
account in October and November 2026:

- B$250 on the 1st buys 3,320 kWh (600 at 0.01, 1,400 at 0.08, 1,320 at 0.10).
- The B$10 top-up after that buys 100 kWh at 0.10.
- The 1 Nov and 10 Nov balances match USMS's to the cent.
- B$30 of water bought 109.086 m³.
- `water_november_not_fifo`: a **counterexample**. In November, USMS charged
  13.355 m³ of water at B$0.44 (B$5.88) while cheaper October units were
  left. Oldest first predicts B$1.47. For water, the model's assumption
  doesn't hold.

## What isn't proven

- **The Python code itself.** These proofs are about the model. `usage.py` is
  meant to compute the same thing, and its tests check the same real figures,
  but nothing here proves the Python matches, including its rounding to 3
  decimals and its use of floats.
- **How USMS really behaves.** The model assumes USMS prices top-ups by the
  month's purchases and uses units oldest first. The October and November
  electricity readings agree. November's water doesn't (see above).
- **Usage beyond what's left.** `use` charges nothing for units beyond the
  layers. `usage.use_layers` charges them at the last rate, but a meter can't
  go below zero anyway.

## Building

Install Lean with [elan](https://lean-lang.org/install/), then:

```sh
cd verification
lake exe cache get   # Mathlib's prebuilt files, about 5 GB
lake build
```

CI builds it on every change to `verification/` (`.github/workflows/lean.yml`).
