/-
Copyright (c) 2026 Danial Othman. All rights reserved.
Released under MIT license as described in the file LICENSE.
Authors: Danial Othman
-/
import Verification.Layers

/-!
# The model against real USMS figures

Readings from one household's USMS account in October and November 2026,
checked exactly against the model. USMS shows balances to the cent, so
"matches" means within B$0.01.

The last section is the counterexample: November's water readings are **not**
what oldest-first predicts, so for water the model's assumption is wrong
somewhere. A proof only covers what the model assumes.
-/

namespace UZMZ

/-! ## Electricity -/

/-- B$250 on the 1st buys 3,320 kWh: 600 at 0.01, 1,400 at 0.08 and 1,320 at
0.10 (2 Aug, 1 Sep and 1 Nov 2026). -/
theorem b250_buys_3320 : elecCost 3320 = 250 := by
  simp only [elecCost, inBand, above, max_def, min_def]
  norm_num

theorem b250_layers :
    elecSlices 0 3320 = [(600, 1/100), (1400, 8/100), (1320, 10/100), (0, 12/100)] := by
  simp only [elecSlices, inBand, above, max_def, min_def]
  norm_num

/-- The B$10 bridge top-up on 31 Oct, after 3,320 kWh: 100 kWh at 0.10. -/
theorem bridge_b10 : topUp elecCost 3320 100 = 10 := by
  simp only [topUp, elecCost, inBand, above, max_def, min_def]
  norm_num

/-- **There is no early-month bonus.** A month's B$250 + B$10 cost what
3,420 kWh cost, however they're split or timed. -/
theorem october_total :
    topUp elecCost 0 3320 + topUp elecCost 3320 100 = elecCost 3420 := by
  simp only [topUp, elecCost, inBand, above, max_def, min_def]
  norm_num

/-- 1 Nov: 57.855 kWh left from October at 0.10, then the B$250 top-up.
USMS showed B$255.78. -/
def nov1 : Layers := [(57855/1000, 1/10), (600, 1/100), (1400, 8/100), (1320, 1/10)]

theorem nov1_balance : value nov1 = 2557855/10000 ∧ |value nov1 - 25578/100| < 1/100 := by
  simp only [nov1, value]
  norm_num [abs_of_pos]

/-- 10 Nov: 1,059.94 kWh used since 1 Nov, oldest first. USMS showed
B$211.83, with 997.915 kWh at 0.08 and 1,320 at 0.10 left. -/
theorem nov10_layers : (use nov1 (105994/100)).2 = [(997915/1000, 8/100), (1320, 1/10)] := by
  simp only [nov1, use]
  norm_num

theorem nov10_balance : |value (use nov1 (105994/100)).2 - 21183/100| < 1/100 := by
  rw [nov10_layers]
  simp only [value]
  norm_num [abs_of_pos]

/-! ## Water -/

/-- B$30 on 10 Oct bought 109.086 m³: 54.54 at 0.11 and 54.546 at 0.44. -/
theorem water_b30 : |waterCost (109086/1000) - 30| < 1/100 := by
  simp only [waterCost, inBand, above, max_def, min_def]
  norm_num [abs_of_neg, abs_of_pos]

/-- 1 Nov 10:29: 104.762 m³ for B$29.52, so 50.228 m³ at 0.11 then 54.534 at 0.44. -/
def waterNov1 : Layers := [(50228/1000, 11/100), (54534/1000, 44/100)]

theorem waterNov1_balance : |value waterNov1 - 2952/100| < 1/100 := by
  simp only [waterNov1, value]
  norm_num [abs_of_pos]

/-- **Counterexample: November water isn't oldest first.** By 10 Nov, 13.355 m³
were used. Oldest first, that costs B$1.47 at 0.11; USMS took B$5.88
(B$29.52 to B$23.64), which is 13.355 m³ at 0.44. -/
theorem water_november_not_fifo :
    (use waterNov1 (13355/1000)).1 = 146905/100000 ∧
      |13355/1000 * (44/100 : ℚ) - (2952/100 - 2364/100)| < 1/100 ∧
      (use waterNov1 (13355/1000)).1 ≠ 2952/100 - 2364/100 := by
  simp only [waterNov1, use]
  norm_num [abs_of_neg, abs_of_pos]

end UZMZ
