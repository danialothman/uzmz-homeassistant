/-
Copyright (c) 2026 Danial Othman. All rights reserved.
Released under MIT license as described in the file LICENSE.
Authors: Danial Othman
-/
import Verification.Tariff

/-!
# Price layers, used oldest first

The model behind `usage.layers_value`, `usage.use_layers` and the layers
`usage.tariff_slices` adds for a top-up.

A prepaid balance is what the remaining units cost. UZMZ keeps the remaining
units as layers `(units, B$ per unit)`, oldest first, and usage takes units
off the front. These are the properties that make that bookkeeping safe:
value is conserved, units are conserved, nothing goes negative, and
estimating hourly gives the same cost as estimating once.
-/

namespace UZMZ

/-- Remaining units as priced layers, oldest first: `(units, B$ per unit)`. -/
abbrev Layers := List (ℚ × ℚ)

/-- What the remaining units cost: the balance, if the layers are right. -/
def value : Layers → ℚ
  | [] => 0
  | (u, r) :: ls => u * r + value ls

/-- How many units the layers hold. -/
def units : Layers → ℚ
  | [] => 0
  | (u, _) :: ls => u + units ls

/-- Use `x` units from the front: their cost, and the layers left. -/
def use : Layers → ℚ → ℚ × Layers
  | [], _ => (0, [])
  | (u, r) :: ls, x =>
    if x ≤ u then (x * r, (u - x, r) :: ls)
    else (u * r + (use ls (x - u)).1, (use ls (x - u)).2)

/-- Every layer holds a non-negative number of units. -/
def Valid (l : Layers) : Prop := ∀ p ∈ l, 0 ≤ p.1

/-- **Value is conserved.** The balance before usage is exactly what the
usage cost plus the balance after: no B$ appears or disappears. -/
theorem use_conserves (l : Layers) (x : ℚ) :
    value l = (use l x).1 + value (use l x).2 := by
  induction l generalizing x with
  | nil => simp [use, value]
  | cons p ls ih =>
    obtain ⟨u, r⟩ := p
    simp only [use]
    split_ifs with h
    · simp only [value]; ring
    · simp only [value]; rw [ih (x - u)]; ring

/-- **Units are conserved.** Using `x` of the units leaves exactly `x` fewer. -/
theorem use_units (l : Layers) (x : ℚ) (hl : Valid l) (hx : 0 ≤ x) (hxl : x ≤ units l) :
    units (use l x).2 = units l - x := by
  induction l generalizing x with
  | nil => simp only [units] at hxl; simp only [use, units]; linarith
  | cons p ls ih =>
    obtain ⟨u, r⟩ := p
    simp only [units] at hxl
    simp only [use]
    split_ifs with h
    · simp only [units]; ring
    · have hls : Valid ls := fun q hq => hl q (List.mem_cons_of_mem _ hq)
      rw [ih (x - u) hls (by linarith) (by linarith)]
      simp only [units]; ring

/-- **Nothing goes negative.** Valid layers stay valid after any usage. -/
theorem use_valid (l : Layers) (x : ℚ) (hl : Valid l) (hx : 0 ≤ x) :
    Valid (use l x).2 := by
  induction l generalizing x with
  | nil => simp [use, Valid]
  | cons p ls ih =>
    obtain ⟨u, r⟩ := p
    have hls : Valid ls := fun q hq => hl q (List.mem_cons_of_mem _ hq)
    simp only [use]
    split_ifs with h
    · intro q hq
      rcases List.mem_cons.mp hq with rfl | hq
      · simp only; linarith
      · exact hls q hq
    · exact ih (x - u) hls (by have := not_le.mp h; linarith)

/-- **Estimating hourly costs the same as estimating once.** Using `a` units
and then `b` more costs exactly what using `a + b` at once does, so UZMZ's
estimate doesn't depend on how often it polls. -/
theorem use_twice (l : Layers) (a b : ℚ) (hl : Valid l) (ha : 0 ≤ a) (hb : 0 ≤ b) :
    (use l a).1 + (use (use l a).2 b).1 = (use l (a + b)).1 := by
  induction l generalizing a with
  | nil => simp [use]
  | cons p ls ih =>
    obtain ⟨u, r⟩ := p
    have hls : Valid ls := fun q hq => hl q (List.mem_cons_of_mem _ hq)
    by_cases h₁ : a ≤ u
    · by_cases h₂ : a + b ≤ u
      · have : b ≤ u - a := by linarith
        simp only [use, h₁, h₂, this, ite_true]; ring
      · have : ¬ b ≤ u - a := by intro h; exact h₂ (by linarith)
        simp only [use, h₁, h₂, this, ite_true, ite_false]
        rw [show b - (u - a) = a + b - u by ring]; ring
    · have h₂ : ¬ a + b ≤ u := by intro h; exact h₁ (by linarith)
      simp only [use, h₁, h₂, ite_false]
      have ha' : 0 ≤ a - u := by have := not_le.mp h₁; linarith
      rw [show a + b - u = (a - u) + b by ring, ← ih (a - u) hls ha']
      ring

/-! ## A top-up's layers -/

/-- The layers a top-up of `u` kWh adds after `b` were bought this month: the
units that land in each band, at that band's rate (`usage.tariff_slices`,
which also drops empty layers). -/
def elecSlices (b u : ℚ) : Layers :=
  [(inBand 0 600 (b + u) - inBand 0 600 b, 1/100),
   (inBand 600 2000 (b + u) - inBand 600 2000 b, 8/100),
   (inBand 2000 4000 (b + u) - inBand 2000 4000 b, 10/100),
   (above 4000 (b + u) - above 4000 b, 12/100)]

/-- A top-up's layers are worth exactly what it cost: right after a top-up,
the balance goes up by the amount paid. -/
theorem elecSlices_value (b u : ℚ) : value (elecSlices b u) = topUp elecCost b u := by
  simp only [elecSlices, value, topUp, elecCost]
  ring

/-- A top-up's layers hold exactly the units bought. -/
theorem elecSlices_units (b u : ℚ) (hb : 0 ≤ b) (hu : 0 ≤ u) :
    units (elecSlices b u) = u := by
  have p₁ := elec_bands_partition b hb
  have p₂ := elec_bands_partition (b + u) (by linarith)
  simp only [elecSlices, units]
  linarith

/-- A top-up's layers are valid: no band gets a negative number of units. -/
theorem elecSlices_valid (b u : ℚ) (hu : 0 ≤ u) : Valid (elecSlices b u) := by
  have h : b ≤ b + u := by linarith
  have d₁ := inBand_mono 0 600 h
  have d₂ := inBand_mono 600 2000 h
  have d₃ := inBand_mono 2000 4000 h
  have d₄ := above_mono 4000 h
  intro q hq
  simp only [elecSlices, List.mem_cons, List.not_mem_nil, or_false] at hq
  rcases hq with rfl | rfl | rfl | rfl <;> simp only <;> linarith

end UZMZ
