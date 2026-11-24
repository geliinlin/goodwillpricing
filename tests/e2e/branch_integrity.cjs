const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { startApp } = require('./browser_fixture.cjs');

const out = process.env.GW_TEST_RESULTS;
const button = (page, name) => page.getByRole('button', {name, exact: typeof name === 'string'});
async function idle(page) {
  await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
  await page.locator('[data-stale=true]').first().waitFor({state:'detached'});
}
async function choose(page, label, value) {
  await idle(page);
  const field = page.getByRole('combobox', {name:label, exact:true});
  await field.click();
  await field.fill(value);
  await page.getByRole('option', {name:value, exact:true}).click();
  await page.getByRole('listbox').waitFor({state:'hidden'});
}
async function enter(page, name, value) {
  const field = page.getByRole('textbox', {name, exact:true});
  await field.fill(value);
  await field.press('Enter');
}
async function shot(page, filename) {
  await idle(page);
  await page.screenshot({path:path.join(out, filename+'.png'), animations:'disabled'});
}
async function download(page, name, filename) {
  const pending = page.waitForEvent('download');
  await page.getByText(name, {exact:true}).click();
  await (await pending).saveAs(path.join(out,filename));
}
async function historyRow(page, tag, branch) {
  await page.locator('.gw-activity-row').filter({hasText:tag}).filter({hasText:branch}).waitFor();
}

test('Transfer preserves the original store in history, reports and printed tags', {timeout:240000}, async t => {
  const {base, browser, password} = await startApp(t);
  const viewport = {width:1550, height:1080};
  const staff = await browser.newPage({viewport});
  const admin = await browser.newPage({viewport});
  async function login(page, user) {
    await page.getByLabel('Email or Username', {exact:true}).fill(user);
    await page.getByLabel('Password', {exact:true}).fill(password);
    await button(page,'Login').click();
    await button(page,'Logout').waitFor();
  }
  async function price(description, amount) {
    await button(staff,'Price an Item').click();
    await enter(staff,'Item Description (Optional)',description);
    for (const [field,value] of [['Item Type','Clothing'],['Category','Shirts'],['Brand Tier','Generic'],['Condition','Good']]) {
      await choose(staff,field,value);
    }
    await button(staff,'Search Guidelines').click();
    await staff.locator('.gw-suggestion .price').filter({hasText:'$12.50'}).waitFor();
    if (amount !== '12.50') {
      await enter(staff,'Final Price',amount);
      await choose(staff,'Reason for Override','Visible Wear');
    }
    await button(staff,'Submit & Generate Tag').click();
    await staff.getByText('Tag generated successfully', {exact:true}).waitFor();
    assert.equal(await staff.locator('.gw-tag-price').innerText(), '$'+amount);
    return (await staff.locator('.gw-tag-id b').innerText()).trim();
  }
  async function reprint(page, tag, store, file) {
    await button(page,'Reprint an Existing Tag').click();
    await enter(page,'Existing Tag ID',tag);
    await button(page,'Find Tag').click();
    await page.getByText('Saved tag found. No new record has been created.', {exact:true}).waitFor();
    await page.getByText('Store: '+store,{exact:true}).waitFor();
    await download(page,'Download Printable Tag PDF',file+'.pdf');
    await shot(page,file);
  }
  const stages = {};
  function record(stage, current, rows) {
    const state = JSON.parse(execFileSync(process.env.GW_E2E_PYTHON,
      [path.join(__dirname,'run_branch_integrity.py'),'snapshot'],{encoding:'utf8'}));
    assert.equal(state.current_branch,current);
    assert.deepEqual(state.decisions.map(d=>[d.tag,d.price,d.branch_id]),rows);
    stages[stage] = state;
    return state;
  }

  const legacy = 'TS26-100001';
  await staff.goto(base);
  await login(staff,'staff_e2e');
  const first = await price('Before branch transfer','12.50');
  const before = [[legacy,'12.50',null],[first,'12.50',1]];
  record('before_transfer',1,before);
  await shot(staff,'01-original-receipt');

  await admin.goto(base);
  await login(admin,'admin_e2e');
  await button(admin,'Users').click();
  await enter(admin,'Search users','staff_e2e@example.invalid');
  await admin.getByText('1 of 4 accounts shown',{exact:true}).waitFor();
  await idle(admin);
  await admin.locator('[data-testid="stDataFrame"] .dvn-scroller').click({position:{x:16,y:55}});
  await button(admin,'Edit User Profile').click();
  await choose(admin,'Assigned Branch','Test South');
  await button(admin,'Save Profile Changes').click();
  await button(admin,'Edit User Profile').waitFor();
  record('after_transfer',2,before);
  await shot(admin,'02-transfer');
  await button(staff,'History').click();
  await button(staff,'Login').waitFor();
  await login(staff,'staff_e2e');
  const second = await price('After branch transfer','18.00');
  const after = [...before,[second,'18.00',2]];
  const sql = record('after_second_submission',2,after);
  await button(staff,'History').click();
  await historyRow(staff,first,'Test North');
  await historyRow(staff,second,'Test South');
  await shot(staff,'03-staff-history');
  await reprint(staff,first,'Test North','04-original-reprint');

  await button(admin,'Admin Dashboard').click();
  for (const d of sql.decisions) await historyRow(admin,d.tag,d.branch);
  assert.deepEqual(await admin.locator('.gw-kpi-value').allInnerTexts(),sql.dashboard.map(String));
  await shot(admin,'05-dashboard');
  await button(admin,/Open Pricing History/).click();
  for (const [option,tag] of [['Test North (#1)',first],['Test South (#2)',second],['Unknown',legacy]]) {
    await choose(admin,'Branch',option);
    await admin.getByText('1 matching decisions shown',{exact:false}).waitFor();
    await historyRow(admin,tag,sql.decisions.find(d=>d.tag===tag).branch);
  }
  await shot(admin,'06-unknown-history');
  await button(admin,'Reports').click();
  const cards = admin.locator('.gw-report-metric-value');
  await cards.first().waitFor();
  for (const group of sql.reports) {
    await choose(admin,'Branch',group.branch);
    await idle(admin);
    const expected = [String(group.items),'$'+group.avg,group.rate,String(group.staff)];
    assert.deepEqual(await cards.allInnerTexts(),expected,group.branch);
    if (group.branch === 'All branches') {
      await download(admin,'Export to PDF','transfer-report.pdf');
      await shot(admin,'07-report');
    }
  }
  const other = await browser.newPage({viewport});
  await other.goto(base);
  await login(other,'other_e2e');
  await button(other,'History').click();
  await historyRow(other,legacy,'Unknown');
  await reprint(other,legacy,'Unknown','08-legacy-reprint');
  record('after_reprints',2,after);
  await fs.writeFile(path.join(out,'transfer-sql.json'), JSON.stringify(stages,null,2)+'\n');
});
