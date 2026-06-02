import { parseCsvText } from "./src/lib/csv.js";

const r = await fetch("https://huggingface.co/datasets/Algoritmica/green-lion-2026/resolve/main/Hackathon_Data/green_lion_2026_1_synthetic_loan_tape.csv");
const text = await r.text();
const rows = parseCsvText(text);

console.log("performing_status values:", [...new Set(rows.map(r => r.performing_status))]);
console.log("arrears_bucket values:", [...new Set(rows.map(r => r.arrears_bucket))]);
console.log("default_crr_flag values:", [...new Set(rows.map(r => r.default_crr_flag))]);
console.log("foreclosure_flag values:", [...new Set(rows.map(r => r.foreclosure_flag))]);
