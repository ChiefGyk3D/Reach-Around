// SPDX-License-Identifier: Apache-2.0
//
// Entry point of the deploy-time budget check (issue #261): bundled with esbuild
// by the deploy workflow and run with the step's environment. The logic is in
// src/kit/budgetcheck.ts so the Worker tests cover it.
import { runBudgetCheck } from "../src/kit/budgetcheck";

const r = runBudgetCheck(process.env);
for (const l of r.lines) console.log(l);
process.exit(r.code);
