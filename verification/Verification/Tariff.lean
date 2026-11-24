/-
Copyright (c) 2026 Danial Othman. All rights reserved.
Released under MIT license as described in the file LICENSE.
Authors: Danial Othman
-/
import Mathlib

/-!
# Prepaid tariff bands

The model behind `usage.tariff_cost`, `usage.tariff_slices` and
`usage.price_top_up` in `custom_components/uzmz/usage.py`.

USMS prices a prepaid top-up by the units **bought** so far in the calendar
month: the bands below apply to the month's cumulative purchases, and restart
on the 1st. So a month whose top-ups add up to `x` units costs `elecCost x`,
and a top-up of `u` units after `b` units were already bought that month costs
`topUp elecCost b u`.

Everything is in `ℚ`, so the B$ figures are exact.
-/

namespace UZMZ

/-- How much of `x` falls in the band from `lo` to `hi`. -/
def inBand (lo hi x : ℚ) : ℚ := max lo (min x hi) - lo

/-- How much of `x` falls above `lo` (the last, open-ended band). -/
def above (lo x : ℚ) : ℚ := max lo x - lo

/-- DES residential electricity, B$ per kWh: 0.01 up to 600 kWh, 0.08 up to
2,000, 0.10 up to 4,000, then 0.12 (`usage.ELECTRIC_TARIFF`). -/
def elecCost (x : ℚ) : ℚ :=
  1/100 * inBand 0 600 x + 8/100 * inBand 600 2000 x
    + 10/100 * inBand 2000 4000 x + 12/100 * above 4000 x

/-- Domestic water, B$ per m³: 0.11 up to 54.54 m³, then 0.44
(`usage.WATER_TARIFF`). -/
def waterCost (x : ℚ) : ℚ :=
  11/100 * inBand 0 (5454/100) x + 44/100 * above (5454/100) x

/-- What a top-up of `u` units costs after `b` units were bought this month. -/
def topUp (cost : ℚ → ℚ) (b u : ℚ) : ℚ := cost (b + u) - cost b

/-! ## The bands -/

lemma inBand_mono (lo hi : ℚ) : Monotone (inBand lo hi) := by
  intro x y h
  unfold inBand
  gcongr

lemma above_mono (lo : ℚ) : Monotone (above lo) := by
  intro x y h
  unfold above
  gcongr

/-- `above lo` has growing increments: the same `u` units take in at least as
much of the band when added later. -/
lemma above_increment_mono (lo b c u : ℚ) (hbc : b ≤ c) (hu : 0 ≤ u) :
    above lo (b + u) - above lo b ≤ above lo (c + u) - above lo c := by
  simp only [above, max_def]
  split_ifs <;> linarith

/-- The electricity bands split any `x ≥ 0` exactly: no unit is lost or
counted twice. -/
lemma elec_bands_partition (x : ℚ) (hx : 0 ≤ x) :
    inBand 0 600 x + inBand 600 2000 x + inBand 2000 4000 x + above 4000 x = x := by
  simp only [inBand, above, max_def, min_def]
  split_ifs <;> linarith

/-- The water bands split any `x ≥ 0` exactly. -/
lemma water_bands_partition (x : ℚ) (hx : 0 ≤ x) :
    inBand 0 (5454/100) x + above (5454/100) x = x := by
  simp only [inBand, above, max_def, min_def]
  split_ifs <;> linarith

/-! ## The month's cost -/

/-- Buying more never costs less. -/
theorem elecCost_mono : Monotone elecCost := by
  intro x y h
  unfold elecCost
  have := inBand_mono 0 600 h
  have := inBand_mono 600 2000 h
  have := inBand_mono 2000 4000 h
  have := above_mono 4000 h
  linarith

theorem waterCost_mono : Monotone waterCost := by
  intro x y h
  unfold waterCost
  have := inBand_mono 0 (5454/100) h
  have := above_mono (5454/100) h
  linarith

/-- The same, in the closed form `usage.tariff_cost` computes band by band. -/
theorem elecCost_eq_piecewise (x : ℚ) (hx : 0 ≤ x) :
    elecCost x =
      if x ≤ 600 then x / 100
      else if x ≤ 2000 then 6 + (x - 600) * 8 / 100
      else if x ≤ 4000 then 118 + (x - 2000) * 10 / 100
      else 318 + (x - 4000) * 12 / 100 := by
  simp only [elecCost, inBand, above, max_def, min_def]
  split_ifs <;> linarith

/-- The tariff as a base rate plus "hinges" where the rate steps up: B$0.01,
plus 0.07 above 600 kWh, 0.02 above 2,000 and 0.02 above 4,000. -/
lemma elecCost_hinges (x : ℚ) (hx : 0 ≤ x) :
    elecCost x = x / 100 + 7/100 * above 600 x + 2/100 * above 2000 x
      + 2/100 * above 4000 x := by
  rw [elecCost_eq_piecewise x hx]
  simp only [above, max_def]
  split_ifs <;> linarith

/-! ## Top-ups -/

/-- **Splitting a top-up doesn't change its price.** Buying `u₁` then `u₂`
costs exactly what buying `u₁ + u₂` at once does. With `month_total`, the day
of the month doesn't matter either, only what was already bought that month.
(There is no early-month bonus.) -/
theorem topUp_split (cost : ℚ → ℚ) (b u₁ u₂ : ℚ) :
    topUp cost b (u₁ + u₂) = topUp cost b u₁ + topUp cost (b + u₁) u₂ := by
  unfold topUp
  rw [add_assoc]
  ring

/-- A month's top-ups, in any amounts, cost what their total costs: the
month's bill depends only on the units bought in it. -/
theorem month_total (cost : ℚ → ℚ) (us : List ℚ) :
    (us.foldl (fun (acc : ℚ × ℚ) u => (acc.1 + u, acc.2 + topUp cost acc.1 u)) (0, 0)).2
      = cost us.sum - cost 0 := by
  suffices h : ∀ (b s : ℚ), s = cost b - cost 0 →
      (us.foldl (fun (acc : ℚ × ℚ) u => (acc.1 + u, acc.2 + topUp cost acc.1 u)) (b, s)).2
        = cost (b + us.sum) - cost 0 by
    simpa using h 0 0 (by ring)
  induction us with
  | nil => intro b s hs; simpa using hs
  | cons u us ih =>
    intro b s hs
    simp only [List.foldl_cons, List.sum_cons]
    rw [ih (b + u) _ (by unfold topUp; rw [hs]; ring), add_assoc]

/-- Each electricity unit costs between B$0.01 and B$0.12, whatever was bought
before: a top-up of `u` kWh costs at least `u × 0.01` and at most `u × 0.12`. -/
theorem elec_topUp_bounds (b u : ℚ) (hb : 0 ≤ b) (hu : 0 ≤ u) :
    u / 100 ≤ topUp elecCost b u ∧ topUp elecCost b u ≤ u * 12 / 100 := by
  have p₁ := elec_bands_partition b hb
  have p₂ := elec_bands_partition (b + u) (by linarith)
  have d₁ := inBand_mono 0 600 (show b ≤ b + u by linarith)
  have d₂ := inBand_mono 600 2000 (show b ≤ b + u by linarith)
  have d₃ := inBand_mono 2000 4000 (show b ≤ b + u by linarith)
  have d₄ := above_mono 4000 (show b ≤ b + u by linarith)
  unfold topUp elecCost
  constructor <;> linarith

/-- **A later top-up never buys cheaper units.** The same `u` kWh cost at
least as much once more has been bought this month, because the bands' rates
only go up. -/
theorem elec_later_costs_more (b c u : ℚ) (hb : 0 ≤ b) (hbc : b ≤ c) (hu : 0 ≤ u) :
    topUp elecCost b u ≤ topUp elecCost c u := by
  have h₁ := above_increment_mono 600 b c u hbc hu
  have h₂ := above_increment_mono 2000 b c u hbc hu
  have h₃ := above_increment_mono 4000 b c u hbc hu
  unfold topUp
  rw [elecCost_hinges _ (by linarith), elecCost_hinges _ hb,
    elecCost_hinges _ (by linarith), elecCost_hinges _ (by linarith)]
  linarith

end UZMZ
