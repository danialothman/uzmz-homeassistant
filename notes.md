## Top-up advice in plain words

v1.8.0's top-up planner showed three numbers that only make sense if you already know how prepaid pricing works. This release spells out what they mean:

- **Top-up Advice** (one per meter) says it in one sentence, for example *"Up to B$6.00 more this month buys 54.54 m³ at B$0.11/m³; above that, B$0.44/m³. Prices restart on the 1st."*
- **Next Price Change** (one per meter) shows when the units you're using now run out and the next price starts, based on your average use over the last 7 days. For example, it shows when your cheap water runs out and the B$0.44 units start. It works from the units you already have, so it's known straight after setup.
- **Example dashboard:** a tip card for each meter shows both, in place of the three planner rows. The planner sensors are still there for automations.
- **README:**
  - practical advice: top up every month, B$6 of water a month, and spread big electricity top-ups over several months;
  - a new **Top-up reminder** automation that nudges you from the 25th while the month's cheapest units are still unbought.

## Upgrading

Update through HACS and restart Home Assistant. If you use the example dashboard, paste it again to get the tip cards.
