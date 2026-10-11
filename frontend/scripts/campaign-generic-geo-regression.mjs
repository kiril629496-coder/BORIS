import fs from "node:fs";

const overview = fs.readFileSync("/root/BORIS/frontend/app/dashboard/campaign/page.tsx","utf8");
const workspace = fs.readFileSync("/root/BORIS/frontend/app/dashboard/campaign/[id]/page.tsx","utf8");

const forbiddenCities = ["Москва|Санкт-Петербург|СПб|Сочи|Уфа|Пенза|Ижевск|Стерлитамак","Уфа и область"];
for (const token of forbiddenCities) {
  if (overview.includes(token) || workspace.includes(token)) {
    throw new Error("campaign city-specific hardcode remains: "+token);
  }
}
if (!overview.includes('(?:город|г\\.)')) {
  throw new Error("generic explicit city label parser missing");
}
if (!workspace.includes('(?:область|край|республика)')) {
  throw new Error("generic region heading parser missing");
}
console.log("CAMPAIGN_GENERIC_FRONTEND_GEO=PASS");
