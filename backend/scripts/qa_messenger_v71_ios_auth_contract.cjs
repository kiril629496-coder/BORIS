const fs = require("fs"), vm = require("vm"), ts = require("/root/BORIS/frontend/node_modules/typescript");
const root="/root/BORIS/frontend/";
const source=fs.readFileSync(root+"app/login/page.tsx","utf8");
const ast=ts.createSourceFile("login.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let fn;
function visit(n){if(ts.isVariableDeclaration(n)&&n.name.getText(ast)==="finishLogin")fn=n.initializer.getText(ast);ts.forEachChild(n,visit);}
visit(ast);if(!fn)throw Error("finishLogin missing");
async function check(path,stored,want,status="active"){
 const state=new Map(stored?[["boris_post_login_return",stored]]:[]), routes=[];
 const ctx={localStorage:{getItem:k=>state.get(k)||null,removeItem:k=>state.delete(k)},window:{location:{pathname:path}},persistSession:()=>{},rememberVerification:()=>{},router:{push:p=>routes.push(p)},resolvePostAuthRoute:async()=>"/dashboard"};
 vm.createContext(ctx);
 const js=ts.transpileModule("const run = "+fn+";",{compilerOptions:{target:ts.ScriptTarget.ES2020}}).outputText;
 vm.runInContext(js+"globalThis.run=run",ctx);await ctx.run({user:{status}});
 if(routes[0]!==want)throw Error(JSON.stringify({path,stored,want,got:routes}));
 console.log("PASS "+path+" -> "+want);
}
(async()=>{
 await check("/messenger/login",null,"/messenger");
 await check("/messenger/login","/messenger?account_id=qa&internal_conversation_id=7","/messenger?account_id=qa&internal_conversation_id=7");
 await check("/login",null,"/dashboard");
 await check("/login","/phone-app","/phone-app");
 await check("/login","https://evil.example","/dashboard");
 await check("/messenger/login",null,"/verify","pending_verification");
 const meta=fs.readFileSync(root+"app/messenger/layout.tsx","utf8");
 if(!meta.includes('"/messenger/apple-touch-icon.png"'))throw Error("PNG metadata missing");
 const png=fs.readFileSync(root+"public/messenger/apple-touch-icon.png");
 if(png.readUInt32BE(16)!==180||png.readUInt32BE(20)!==180||png.toString("hex",0,8)!=="89504e470d0a1a0a")throw Error("invalid PNG");
 for(const file of ["app/messenger/page.tsx","app/messenger/legal/page.tsx"]){
  if(fs.readFileSync(root+file,"utf8").includes('window.location.replace("/login")'))throw Error("out of scope login "+file);
 }
 console.log("PWA_SCOPE_PNG=PASS");
})().catch(e=>{console.error(e);process.exit(1)});
