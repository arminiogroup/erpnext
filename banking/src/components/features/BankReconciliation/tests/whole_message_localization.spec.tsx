import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { delimiter, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { test } = require("@playwright/test");
const banking = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const esbuild = createRequire(resolve(banking, "../../frappe/package.json"))("esbuild");
const TARGETS = {
  "A difference from your bank statement may indicate transactions that still need reconciliation.": "Una differenza rispetto all’estratto conto può indicare movimenti ancora da riconciliare.",
  "For more information, open the **Reconciliation Statement** tab.": "Per maggiori informazioni, aprire la scheda **Prospetto di riconciliazione**.",
  "Reconciliation Statement": "Prospetto di riconciliazione",
  "The following documents will be **cancelled**:": "I seguenti documenti saranno **annullati**:",
  "Reference Documents": "Documenti di riferimento",
  "Get Outstanding Documents": "Recupera documenti con importi residui",
  "Select Outstanding Documents": "Seleziona documenti con importi residui",
  "Outstanding documents for {0}. Payment amount: {1}.": "Documenti con importi residui per {0}. Importo del pagamento: {1}.",
  "Document Type": "Tipo di documento", "Document Name": "Nome del documento",
  "Invoice No": "N. fattura", "Due Date": "Data di scadenza",
  "Document Amount": "Importo del documento", Outstanding: "Importo residuo",
  "Selected rows: {0}": "Righe selezionate: {0}",
  "Total outstanding: {0}": "Importo residuo totale: {0}",
  Cancel: "Annulla", Select: "Seleziona",
  "Sales Invoice": "Fattura di vendita", "Purchase Invoice": "Fattura di acquisto",
  "Journal Entry": "Registrazione contabile", "Payment Entry": "Registrazione di pagamento",
};

type Input = {
  mode: string;
  rows?: Array<Record<string, unknown>>;
  payments?: Array<Record<string, unknown>>;
  references?: Array<Record<string, unknown>>;
  partyName?: string;
  messages?: Record<string, string>;
  loading?: boolean;
  error?: boolean;
  actionLoading?: boolean;
};
type Locator = { first(): Locator; nth(index: number): Locator; hover(): Promise<void>; click(): Promise<void>; waitFor(options: { state: string }): Promise<void> };
type BrowserPage = {
  on(event: "pageerror", callback: (error: Error) => void): void;
  route(pattern: string, callback: (route: { fulfill(options: { status: number; contentType: string; body: string }): Promise<unknown> }) => Promise<unknown>): Promise<unknown>;
  goto(url: string): Promise<unknown>;
  addScriptTag(options: { content: string }): Promise<unknown>;
  evaluate<T, A>(callback: (argument: A) => T, argument: A): Promise<Awaited<T>>;
  locator(selector: string): Locator;
};
declare global {
  interface Window {
    bankingWholeMessages: { mount(input: Input): void; input: Input; observations: { actions: number; queries: unknown[] }; expectedAmount(value: number, currency?: string): string };
  }
}

const memorySDK = `import React from "react";
const fail=()=>{window.bankingWholeMessages.observations.actions++;throw Error("Unexpected business action")};
export const FrappeContext=React.createContext({call:{get:fail},file:{uploadFile:fail}});
export function useFrappePostCall(){return {call:fail,loading:!!window.bankingWholeMessages.input.actionLoading,error:undefined};}
export function useSWRConfig(){return {mutate:fail};}
export function useFrappeGetDoc(doctype,name){return {data:{name,withdrawal:0,deposit:24,currency:"EUR",payment_entries:window.bankingWholeMessages.input.payments??[]},isLoading:false,error:undefined};}
export function useFrappeGetCall(method,args){const i=window.bankingWholeMessages.input;window.bankingWholeMessages.observations.queries.push({method,args});return {data:{message:i.rows??[]},isLoading:!!i.loading,error:i.error?{message:"Memory read error"}:undefined};}
export function useFrappeGetDocCount(){return {data:10};}
export function useFrappeGetDocList(){return {data:[],isLoading:false};}
export function useFrappeEventListener(){}
`;
const memoryUtils = `
const fail=()=>{window.bankingWholeMessages.observations.actions++;throw Error("Unexpected recalculation/action")};
export function useGetAccountOpeningBalance(){return {data:{message:100},isLoading:false};}
export function useGetAccountClosingBalance(){return {data:{message:900.25},isLoading:false};}
export function useGetAccountClosingBalanceAsPerStatement(){return {data:{message:{balance:900.25,date:"2026-10-21"}},isLoading:false};}
export function useGetUnreconciledTransactions(){return {data:{message:[]},mutate:fail};}
export function useGetRuleForTransaction(){return {data:null};}
export function useRefreshUnreconciledTransactions(){return fail;}
export function useUpdateActionLog(){return fail;}
`;
const bundle = (await esbuild.build({
  stdin: { loader: "tsx", resolveDir: banking, contents: `
import React from "react";import {createRoot} from "react-dom/client";import {flushSync} from "react-dom";
import {Provider,createStore} from "jotai";import {FormProvider,useForm} from "react-hook-form";
import {AlertDialog,AlertDialogContent,AlertDialogTitle,AlertDialogDescription} from "./src/components/ui/alert-dialog";
import {MemoryRouter} from "react-router";import {TooltipProvider} from "./src/components/ui/tooltip";
import {BankRecWorkspace} from "./src/pages/BankReconciliation";
import Unreconcile from "./src/components/features/BankReconciliation/BankTransactionUnreconcileModalBody";
import {GetUnpaidInvoicesButton,InvoicesSection,isUnpaidInvoicesButtonOpen} from "./src/components/features/BankReconciliation/RecordPaymentModalContent";
import {selectedBankAccountAtom,bankRecUnreconcileModalAtom} from "./src/components/features/BankReconciliation/bankRecAtoms";
import {formatCurrency} from "./src/lib/numbers";
let root,store;
function PaymentView(){const input=window.bankingWholeMessages.input;const form=useForm({defaultValues:{company:"Raw Company",posting_date:"2026-10-21",payment_type:"Pay",party_type:"Supplier",party:"RAW-PARTY",party_name:input.partyName??'Authored <party> & "name"',paid_amount:24,received_amount:24,paid_to:"RAW-PARTY-ACCOUNT",paid_from:"RAW-BANK-ACCOUNT",unallocated_amount:0,total_allocated_amount:0,difference_amount:0,references:input.references??[],deductions:[]}});return <FormProvider {...form}>{input.mode==="parent"?<InvoicesSection currency="EUR"/>:<GetUnpaidInvoicesButton currency="EUR"/>}</FormProvider>;}
window.bankingWholeMessages={input:{mode:"balance"},observations:{actions:0,queries:[]},expectedAmount:formatCurrency,mount(input){
 window.frappe._messages=input.messages??{};if(root)flushSync(()=>root.unmount());window.localStorage.clear();this.input=structuredClone(input);this.observations={actions:0,queries:[]};store=createStore();
 store.set(selectedBankAccountAtom,{name:"BANK-RAW",company:"Raw Company",account_name:"Authored <bank> & name",account:"RAW-GL",account_currency:"EUR"});store.set(bankRecUnreconcileModalAtom,"BT-RAW");store.set(isUnpaidInvoicesButtonOpen,input.mode==="picker");document.body.innerHTML='<div id="root"></div>';root=createRoot(document.getElementById("root"));
 flushSync(()=>root.render(<Provider store={store}><MemoryRouter><TooltipProvider>{input.mode==="balance"?<BankRecWorkspace/>:input.mode==="unreconcile"?<AlertDialog open><AlertDialogContent><AlertDialogTitle>Memory transaction</AlertDialogTitle><AlertDialogDescription>Memory transaction references</AlertDialogDescription><Unreconcile/></AlertDialogContent></AlertDialog>:<PaymentView/>}</TooltipProvider></MemoryRouter></Provider>));}};
` },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
  tsconfig: resolve(banking, "tsconfig.app.json"), nodePaths: (process.env.NODE_PATH ?? "").split(delimiter).filter(Boolean),
  define: { "process.env.NODE_ENV": '"development"', "import.meta.glob": "globalThis.bankingLocaleChunks" },
  banner: { js: "globalThis.bankingLocaleChunks=()=>({});" },
  plugins: [{ name: "external-memory-leaves-and-test-only-exports", setup(build: {
    onResolve(options: { filter: RegExp }, callback: () => { path: string; namespace: string }): void;
    onLoad(options: { filter: RegExp; namespace?: string }, callback: (args: { path: string }) => { contents: string; loader: string; resolveDir?: string } | undefined): void;
  }) {
    build.onResolve({ filter: /^frappe-react-sdk$/ }, () => ({ path: "sdk", namespace: "memory" }));
    build.onLoad({ filter: /.*/, namespace: "memory" }, () => ({ contents: memorySDK, loader: "jsx", resolveDir: banking }));
    build.onLoad({ filter: /[/\\]BankReconciliation[/\\]utils\.ts$/ }, () => ({ contents: memoryUtils, loader: "ts" }));
    build.onLoad({ filter: /[/\\]pages[/\\]BankReconciliation\.tsx$/ }, ({ path }) => ({ contents: readFileSync(path,"utf8")+"\nexport {BankRecWorkspace};", loader: "tsx" }));
    build.onLoad({ filter: /[/\\]RecordPaymentModalContent\.tsx$/ }, ({ path }) => ({ contents: readFileSync(path,"utf8")+"\nexport {GetUnpaidInvoicesButton,InvoicesSection,isUnpaidInvoicesButtonOpen};", loader: "tsx" }));
    build.onLoad({ filter: /[/\\](BankPicker|BankRecDateFilter|BankTransactionUnreconcileModal|CompanySelector|MatchAndReconcile|BankReconciliationStatement|BankTransactionList|BankClearanceSummary|IncorrectlyClearedEntries|SelectedTransactionDetails|SelectedTransactionsTable|Settings|ActionLog|FileUploadBanner)\.tsx$/ }, () => ({ contents: "export default function MemoryPresentationLeaf(){return null}", loader: "jsx" }));
    build.onLoad({ filter: /[/\\]ui[/\\]form-elements\.tsx$/ }, () => ({ contents: `import React from "react";export function AccountFormField(){return null}export function CurrencyFormField(){return null}export function DataField(){return null}export function DateField(){return null}export function LinkFormField(){return null}export function PartyTypeFormField(){return null}export function SmallTextField(){return null}`, loader: "jsx" }));
    build.onLoad({ filter: /[/\\]ui[/\\]error-banner\.tsx$/ }, () => ({ contents: `import React from "react";export default function ErrorBanner(){return <div data-memory-error>Memory read error</div>}`, loader: "jsx" }));
    build.onLoad({ filter: /[/\\]hooks[/\\]usePaymentEntryCalculations\.tsx$/ }, () => ({ contents: `const fail=()=>{window.bankingWholeMessages.observations.actions++;throw Error("Unexpected allocation")};export function usePaymentEntryCalculations(){return {allocatePartyAmount:fail,setTotalAllocatedAmount:fail,setUnallocatedAmount:fail}}`, loader: "ts" }));
  }}],
})).outputFiles[0].text;

const errors = new WeakMap<object, string[]>();
test.beforeEach(async ({page}:{page:BrowserPage}) => {const e:string[]=[];errors.set(page,e);page.on("pageerror",error=>e.push(String(error)));});
test.afterEach(async ({page}:{page:BrowserPage}) => {assert.deepEqual(errors.get(page),[]);assert.equal(await page.evaluate(()=>window.bankingWholeMessages.observations.actions,undefined),0);});
async function mount(page:BrowserPage,input:Input){
  await page.route("**/*",route=>route.fulfill({status:200,contentType:"text/html",body:"<!doctype html><html><body></body></html>"}));
  await page.goto("http://banking-memory.example.test/");
  await page.addScriptTag({content:`window.locals={":Currency":{EUR:{symbol:"€",number_format:"#,###.##"}},":Company":{"Raw Company":{default_currency:"EUR"}}};window.frappe={_messages:{},boot:{lang:"it",user:{defaults:{company:"Raw Company",date_format:"yyyy-mm-dd"}},sysdefaults:{number_format:"#,###.##",float_precision:2,date_format:"yyyy-mm-dd",currency:"EUR"},currencies:{EUR:{symbol:"€",fraction_units:100}}}};`});
  await page.addScriptTag({content:bundle});
  await page.evaluate(i=>window.bankingWholeMessages.mount(i),{messages:TARGETS,...input});
  await page.evaluate(()=>new Promise<void>(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>done()))),undefined);
}
async function text(page:BrowserPage){return page.evaluate(()=>document.body.textContent??"",undefined);}
const row=(type="Sales Invoice",id='RAW-<&"',outstanding=12.5)=>({voucher_type:type,voucher_no:id,bill_no:"AUTHORED-BILL",due_date:"2026-10-21",invoice_amount:100.25,outstanding_amount:outstanding,currency:"EUR"});

test("balance guidance names the actual visible tab and keeps inline strong emphasis",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"balance"});await page.locator('[data-slot="hover-card-trigger"]').first().hover();await page.locator('[data-slot="hover-card-content"]').waitFor({state:"visible"});
 const r=await page.evaluate(()=>({tab:Array.from(document.querySelectorAll('[role="tab"]')).find(n=>n.textContent?.includes("Prospetto"))?.textContent?.trim(),strong:document.querySelector('[data-slot="hover-card-content"] strong')?.textContent,body:document.querySelector('[data-slot="hover-card-content"]')?.textContent,nested:document.querySelectorAll("p p").length}),undefined);
 assert.equal(r.tab,"Prospetto di riconciliazione");assert.equal(r.strong,r.tab);assert.ok(r.body?.includes("Una differenza rispetto all’estratto conto può indicare"));assert.equal(r.nested,0);
});
test("balance missing dictionary retains the complete truthful English instruction",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"balance",messages:{}});await page.locator('[data-slot="hover-card-trigger"]').first().hover();await page.locator('[data-slot="hover-card-content"]').waitFor({state:"visible"});
 const t=await text(page);assert.ok(t.includes("For more information, open the Reconciliation Statement tab."));assert.ok(t.includes("A difference from your bank statement may indicate"));assert.ok(!t.includes("tab below."));
});
test("unreconcile warning emphasizes the Italian verb and lists only created vouchers with raw identities",async({page}:{page:BrowserPage})=>{
 const payments=[{name:"A",payment_document:"Payment Entry",payment_entry:'PAY-<&"',allocated_amount:12,reconciliation_type:"Voucher Created"},{name:"B",payment_document:"Journal Entry",payment_entry:"MATCHED-RAW",allocated_amount:5,reconciliation_type:"Matched"}];
 await mount(page,{mode:"unreconcile",payments});const r=await page.evaluate(()=>({strong:Array.from(document.querySelectorAll("strong"),n=>n.textContent),list:Array.from(document.querySelectorAll("ol li"),n=>n.textContent),links:Array.from(document.querySelectorAll("a"),n=>n.getAttribute("href")),injected:document.querySelectorAll("pay-raw,img,script[src]").length,state:JSON.stringify(window.bankingWholeMessages.input.payments)}),undefined);
 assert.deepEqual(r.strong,["annullati"]);assert.deepEqual(r.list,['Registrazione di pagamento: PAY-<&"']);assert.ok(r.links.some(v=>v?.includes('PAY-<&"')));assert.equal(r.injected,0);assert.equal(r.state,JSON.stringify(payments));
});
test("matched-only reconciliation has no cancellation warning or invented created document",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"unreconcile",payments:[{name:"A",payment_document:"Journal Entry",payment_entry:"JE-RAW",allocated_amount:2,reconciliation_type:"Matched"}]});
 assert.equal(await page.evaluate(()=>document.querySelectorAll("ol li,strong").length,undefined),0);assert.ok((await text(page)).includes("JE-RAW"));
});
test("picker whole description reorders raw party and amount without interpreting authored markup",async({page}:{page:BrowserPage})=>{
 const party='<img id="party-injected" src=x> & "name"';const messages={...TARGETS,"Outstanding documents for {0}. Payment amount: {1}.":"Importo del pagamento: {1}. Documenti per {0}."};
 await mount(page,{mode:"picker",partyName:party,messages,rows:[row()]});const r=await page.evaluate(()=>({text:document.querySelector('[data-slot="dialog-description"]')?.textContent,injected:document.querySelectorAll("#party-injected").length,amount:window.bankingWholeMessages.expectedAmount(24,"EUR")}),undefined);
 assert.equal(r.text,`Importo del pagamento: ${r.amount}. Documenti per ${party}.`);assert.equal(r.injected,0);
});
test("picker labels schema types while canonical voucher IDs, links and signed row amounts stay exact",async({page}:{page:BrowserPage})=>{
 const rows=[row(),row("Purchase Invoice","PUR-RAW",-3.25),row("Journal Entry","JE-RAW",0),row("Custom Schema","CUSTOM-RAW",1)];await mount(page,{mode:"picker",rows});
 const r=await page.evaluate(()=>({headers:Array.from(document.querySelectorAll("th"),n=>n.textContent?.trim()),types:Array.from(document.querySelectorAll("tbody tr"),n=>n.children[1]?.textContent?.trim()),ids:Array.from(document.querySelectorAll("tbody a"),n=>[n.textContent,n.getAttribute("href")]),state:JSON.stringify(window.bankingWholeMessages.input.rows)}),undefined);
 assert.deepEqual(r.headers.slice(1),["Tipo di documento","Nome del documento","N. fattura","Data di scadenza","Importo del documento","Importo residuo"]);assert.deepEqual(r.types,["Fattura di vendita","Fattura di acquisto","Registrazione contabile","Custom Schema"]);assert.equal(r.ids[0][0],rows[0].voucher_no);assert.equal(r.ids[0][1],`/desk/sales-invoice/${rows[0].voucher_no}`);assert.equal(r.state,JSON.stringify(rows));
});
test("two payment-term rows count as two selected rows and retain the existing signed outstanding sum",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"picker",rows:[{...row("Sales Invoice","SAME-ID",20.5),payment_term:"First"},{...row("Sales Invoice","SAME-ID",-5.25),payment_term:"Second"}]});await page.locator('thead [role="checkbox"]').click();
 const r=await page.evaluate(()=>({text:document.body.textContent??"",numbers:Array.from(document.querySelectorAll('span.text-ink-gray-5 > span.font-numeric'),n=>n.textContent),sum:window.bankingWholeMessages.expectedAmount(15.25,"EUR")}),undefined);
 assert.ok(r.text.includes("Righe selezionate: 2"));assert.ok(r.text.includes(`Importo residuo totale: ${r.sum}`));assert.deepEqual(r.numbers,["2",r.sum]);
});
test("empty selection uses zero and original currency fallback with complete labels",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"picker",rows:[]});const r=await page.evaluate(()=>({body:document.body.textContent??"",zero:window.bankingWholeMessages.expectedAmount(0,"EUR"),tables:document.querySelectorAll("table").length}),undefined);
 assert.ok(r.body.includes("Righe selezionate: 0"));assert.ok(r.body.includes(`Importo residuo totale: ${r.zero}`));assert.equal(r.tables,0);
});
test("parent reference table uses the same document-amount caption and preserves reference fields",async({page}:{page:BrowserPage})=>{
 const references=[{reference_doctype:"Journal Entry",reference_name:"JE-RAW",bill_no:"EXT-RAW",due_date:"2026-10-21",total_amount:100.25,outstanding_amount:12.5,allocated_amount:0}];await mount(page,{mode:"parent",references});
 const r=await page.evaluate(()=>({body:document.body.textContent??"",headers:Array.from(document.querySelectorAll("th"),n=>n.textContent),link:document.querySelector("tbody a")?.textContent,href:document.querySelector("tbody a")?.getAttribute("href"),state:JSON.stringify(window.bankingWholeMessages.input.references)}),undefined);
 assert.ok(r.body.includes("Documenti di riferimento"));assert.ok(r.headers.includes("Importo del documento"));assert.equal(r.link,"Registrazione contabile: JE-RAW");assert.equal(r.href,"/desk/journal-entry/JE-RAW");assert.equal(r.state,JSON.stringify(references));
});
test("fixed warning markdown cannot create arbitrary HTML and retains translated emphasis",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"unreconcile",payments:[{name:"A",payment_document:"Payment Entry",payment_entry:"RAW",reconciliation_type:"Voucher Created"}],messages:{...TARGETS,"The following documents will be **cancelled**:":'<img id="markdown-injected" src=x> I documenti saranno **annullati**:'}});
 const r=await page.evaluate(()=>({strong:document.querySelector("strong")?.textContent,injected:document.querySelectorAll("#markdown-injected").length,nested:document.querySelectorAll("span p").length}),undefined);assert.equal(r.strong,"annullati");assert.equal(r.injected,0);assert.equal(r.nested,0);
});
test("picker loading and error delivery retain existing disabled action boundaries without execution",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"picker",loading:true,error:true,actionLoading:true});const r=await page.evaluate(()=>({error:document.querySelector('[data-memory-error]')?.textContent,buttons:Array.from(document.querySelectorAll('[data-slot="dialog-footer"] button'),n=>(n as HTMLButtonElement).disabled),queries:window.bankingWholeMessages.observations.queries}),undefined);
 assert.equal(r.error,"Memory read error");assert.deepEqual(r.buttons,[true,true]);assert.ok(JSON.stringify(r.queries).includes('"get_outstanding_invoices":true'));
});
test("picker English fallback and missing schema translation preserve raw labels and values",async({page}:{page:BrowserPage})=>{
 await mount(page,{mode:"picker",messages:{},rows:[row("Custom Schema","RAW-ID")]});const t=await text(page);assert.ok(t.includes("Select Outstanding Documents"));assert.ok(t.includes("Selected rows: 0"));assert.ok(t.includes("Custom Schema"));assert.ok(t.includes("RAW-ID"));assert.ok(t.includes("Document Amount"));
});
