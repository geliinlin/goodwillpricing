const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { startApp } = require('./browser_fixture.cjs');

const out = path.resolve(__dirname, '../../test-results/cross-role-integration');
const button = (scope, name) => scope.getByRole('button', { name, exact: typeof name === 'string' });

async function idle(page) {
  await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
  await page.locator('[data-stale=true]').first().waitFor({state: 'detached'});
}

async function choose(page, scope, label, value) {
  await idle(page);
  const field = scope.getByRole('combobox', {name: label, exact: true});
  await field.click();
  await field.fill(value);
  await page.getByRole('option', {name: value, exact: true}).click();
  await page.getByRole('listbox').waitFor({state: 'hidden'});
  assert.equal(await field.inputValue(), value);
}

async function enter(page, label, value) {
  const input = page.getByRole('textbox', {name: label, exact: true});
  await input.fill(value);
  await input.press('Enter');
}

test('Guideline versions, stale drafts, history, reprint and active staff sessions', {timeout: 300000}, async t => {
  const {base, browser, password} = await startApp(t);
  const admin = await browser.newPage({viewport: {width: 1550, height: 1080}});
  const staff = await browser.newPage({viewport: {width: 1550, height: 1080}});
  const editor = admin.locator('.st-key-gw_guideline_editor_panel');
  const submit = button(staff, 'Submit & Generate Tag');
  const evidence = {};
  function checkpoint(stage, expectedRows) {
    const raw = execFileSync(process.env.GW_E2E_PYTHON, [path.join(__dirname, 'run_cross_role.py'), 'snapshot', stage],
      {encoding: 'utf8'});
    const result = JSON.parse(raw);
    for (const key of ['items','decisions','priced_items','unique_tags']) {
      assert.equal(result.counts[key], expectedRows, stage + ' ' + key);
    }
    evidence[stage] = result;
    return result;
  }
  async function shot(page, name) {
    await idle(page);
    await page.screenshot({path: path.join(out, name+'.png'), animations: 'disabled'});
  }
  async function credentials(page, name) {
    await page.getByLabel('Email or Username', {exact: true}).fill(name);
    await page.getByLabel('Password', {exact: true}).fill(password);
    await button(page, 'Login').click();
  }
  async function login(page, name) {
    await credentials(page, name);
    await button(page, 'Logout').waitFor();
  }
  async function attributes(description, furniture = true) {
    await enter(staff, 'Item Description (Optional)', description);
    for (const [label, value] of [['Item Type', furniture ? 'Furniture' : 'Clothing'],
      ['Category', furniture ? 'Chairs' : 'Shirts'], ['Brand Tier','Generic'], ['Condition','Good']]) {
      await choose(staff, staff, label, value);
    }
  }
  async function quote(price) {
    await button(staff, 'Search Guidelines').click();
    await staff.locator('.gw-suggestion .price').filter({hasText:price}).waitFor();
  }
  async function receipt(price) {
    await staff.getByText('Tag generated successfully', {exact:true}).waitFor();
    assert.equal(await staff.locator('.gw-tag-price').innerText(), price);
    return (await staff.locator('.gw-tag-id b').innerText()).trim();
  }
  async function selectGuide(id) {
    await idle(admin);
    await admin.locator('[data-testid="stDataFrame"] .dvn-scroller').first().click({position:{x:16,y:55}});
    await editor.getByRole('heading', {name:'Update Guideline #'+id, exact:true}).waitFor();
  }
  async function saveGuide(price, id) {
    await editor.getByLabel('Suggested price ($)', {exact:true}).fill(price);
    await button(editor,'Save Guideline').click();
    await admin.getByText('Guideline #'+id+' saved successfully.', {exact:true}).waitFor();
  }
  await admin.goto(base);
  await staff.goto(base);
  await login(admin, 'admin_e2e');
  await login(staff, 'staff_e2e');
  checkpoint('initial', 1);
  await button(admin, 'Guidelines').click();
  await button(admin, /Add New Guideline/).waitFor();
  await idle(admin);
  await button(admin, /Add New Guideline/).click();
  const category = editor.locator('[class*="st-key-gw_form_new_"][class*="_Category_Id_"]');
  const oldClass = await category.evaluate(e => [...e.classList].find(c => c.startsWith('st-key-')));
  await choose(admin, editor, 'Item type', 'Furniture');
  await editor.locator('.'+oldClass).waitFor({state:'detached'});
  await choose(admin, editor, 'Category', 'Chairs');
  await saveGuide('15.00', 3);
  await button(staff, 'Price an Item').click();
  await attributes('Cross-role accepted chair');
  await quote('$15.00');
  await shot(staff, '01-new-quote');
  const created = checkpoint('created', 1);
  assert.deepEqual(created.versions, [{id:3, price:'15.00', active:1, admin_id:1, category:'Chairs',
    item_type:'Furniture', brand:'Generic', item_condition:'Good'}]);
  await submit.dblclick();
  const accepted = await receipt('$15.00');
  const acceptedRows = checkpoint('accepted', 2).decisions;
  assert.equal(acceptedRows[1].tag, accepted);
  const first = acceptedRows[1];
  assert.deepEqual([first.guideline_id,first.suggested,first.final,first.reason,first.staff_id],
    [3,'15.00','15.00',null,2]);

  await button(staff, 'Start Next Item').click();
  await attributes('Cross-role override chair');
  await quote('$15.00');
  await selectGuide(3);
  await saveGuide('18.00', 4);
  const replaced = checkpoint('replaced', 2);
  assert.deepEqual(replaced.versions.map(v => [v.id,v.price,v.active]), [[3,'15.00',0],[4,'18.00',1]]);
  assert.deepEqual(replaced.decisions, acceptedRows);
  await submit.click();
  await staff.getByText(/suggested price has changed/).waitFor();
  assert.deepEqual(checkpoint('stale-price', 2).decisions, acceptedRows);
  await idle(staff);
  assert.ok(await submit.isDisabled(), 'Rejected old price still enables Submit');
  await shot(staff, '02-stale-price');
  await quote('$18.00');
  await enter(staff, 'Final Price', '14.00');
  await choose(staff, staff, 'Reason for Override', 'Visible Wear');
  await shot(staff, '03-new-price-override');
  await submit.click();
  const overridden = await receipt('$14.00');
  const saved = checkpoint('override', 3);
  assert.deepEqual(saved.decisions.slice(0,2), acceptedRows);
  const row = saved.decisions[2];
  assert.deepEqual([row.tag,row.guideline_id,row.suggested,row.final,row.reason,row.staff_id],
    [overridden,4,'18.00','14.00','Visible Wear',2]);

  await button(staff, 'Start Next Item').click();
  await attributes('Cross-role withdrawn draft');
  await quote('$18.00');
  await selectGuide(4);
  await button(editor, 'Deactivate Guideline').click();
  await admin.getByText('Guideline #4 is now inactive.', {exact:true}).waitFor();
  await submit.click();
  await staff.getByText(/guideline is missing or ambiguous/).waitFor();
  await idle(staff);
  assert.ok(await submit.isDisabled(), 'Withdrawn guideline still enables Submit');
  await button(staff, 'Search Guidelines').click();
  await staff.getByText(/No active guideline matches/).waitFor();
  await shot(staff, '04-withdrawn');
  const withdrawn = checkpoint('withdrawn', 3);
  assert.deepEqual(withdrawn.decisions, saved.decisions);
  assert.deepEqual(withdrawn.versions.map(v => v.active), [0,0]);

  await button(staff, 'History').click();
  for (const tag of [accepted,overridden]) await staff.locator('.gw-activity-row').filter({hasText:tag}).waitFor();
  await button(staff, 'Reprint an Existing Tag').click();
  await enter(staff, 'Existing Tag ID', accepted);
  await button(staff, 'Find Tag').click();
  await staff.getByText('Saved tag found. No new record has been created.', {exact:true}).waitFor();
  const pending = staff.waitForEvent('download');
  await staff.getByText('Download Printable Tag PDF', {exact:true}).click();
  const file = await pending;
  assert.equal(file.suggestedFilename(), 'goodwill_'+accepted+'.pdf');
  await file.saveAs(path.join(out,'historical-tag.pdf'));
  await shot(staff, '05-reprint');

  await button(admin, 'Admin Dashboard').click();
  await admin.locator('.gw-activity-row').filter({hasText:overridden}).waitFor();
  assert.deepEqual(await admin.locator('.gw-kpi-value').allInnerTexts(),
    ['active_guides','active_users','decisions','overrides'].map(k => String(withdrawn.counts[k])));
  const latest = [...withdrawn.decisions].sort((a,b) =>
    b.saved_at.localeCompare(a.saved_at) || b.decision_id - a.decision_id);
  const dashboardRows = await admin.locator('.gw-activity-row:not(.gw-activity-head)').allInnerTexts();
  assert.equal(dashboardRows.length, latest.length);
  latest.forEach((decision, index) => {
    for (const value of [decision.tag,decision.staff,decision.branch,decision.display_time,'$'+decision.final]) {
      assert.ok(dashboardRows[index].includes(value), value);
    }
  });
  await shot(admin, '06-dashboard');
  await button(admin, /Open Pricing History/).click();
  await enter(admin, 'Tag ID / item description', 'Cross-role');
  await choose(admin, admin, 'Branch', 'Test North (#1)');
  await choose(admin, admin, 'Staff', 'Fixture staff_e2e (#2)');
  await choose(admin, admin, 'Date', 'Last 7 days');
  await admin.getByText('2 matching decisions shown', {exact:false}).waitFor();
  for (const decision of saved.decisions.slice(1)) {
    const entry = admin.locator('.gw-activity-row').filter({hasText:decision.tag});
    const values = [decision.description, decision.tag, decision.staff, decision.branch, decision.display_time,
      'Suggested $'+decision.suggested, '$'+decision.final];
    if (decision.reason) values.push('Override reason: '+decision.reason);
    const displayed = await entry.innerText();
    for (const value of values) assert.ok(displayed.includes(value), value);
  }
  await shot(admin, '07-history');
  await choose(admin, admin, 'Price Change', 'Override');
  await admin.getByText('1 matching decisions shown', {exact:false}).waitFor();
  assert.equal(await admin.locator('.gw-activity-row').filter({hasText:overridden}).count(),1);
  await choose(admin, admin, 'Branch', 'Test South (#2)');
  await admin.getByText('No pricing decisions match these filters.', {exact:true}).waitFor();

  await button(staff, 'Price an Item').click();
  await attributes('Cross-role account revoked', false);
  await quote('$12.50');
  await button(admin, 'Users').click();
  await enter(admin, 'Search users', 'staff_e2e@example.invalid');
  await admin.getByText('1 of 4 accounts shown', {exact:true}).waitFor();
  await idle(admin);
  await admin.locator('[data-testid="stDataFrame"] .dvn-scroller').click({position:{x:16,y:55}});
  const toggle = admin.getByRole('checkbox', {name:'Active account', exact:true});
  await toggle.waitFor();
  assert.ok(await toggle.isChecked());
  async function accountStatus(active) {
    await admin.getByText('Active account', {exact:true}).click();
    assert.equal(await toggle.isChecked(),active);
    await button(admin, 'Save account status').click();
    await admin.locator('.gw-users-metric').filter({hasText:'Active accounts'}).locator('strong')
      .filter({hasText:active ? /^3$/ : /^2$/}).waitFor();
  }
  await accountStatus(false);
  await submit.click();
  await button(staff, 'Login').waitFor();
  assert.equal(await submit.count(), 0);
  const disabled = checkpoint('account-disabled', 3);
  assert.equal(disabled.employee.active, 0);
  await credentials(staff, 'staff_e2e');
  await staff.getByText(/Invalid email, username or password/).waitFor();
  await shot(staff, '08-disabled-login');
  await accountStatus(true);
  await login(staff, 'staff_e2e');
  await button(staff, 'Price an Item').click();
  await attributes('Cross-role reauthenticated', false);
  await quote('$12.50');
  await shot(staff, '09-reauthenticated');
  assert.equal(await button(staff, 'Users').count(),0);
  const final = checkpoint('reauthenticated', 3);
  assert.deepEqual(final.decisions, saved.decisions);
  assert.equal(final.employee.active, 1);
  assert.ok(Number(final.employee.version) > Number(created.employee.version));
  await submit.click();
  const freshTag = await receipt('$12.50');
  const restored = checkpoint('fresh-submission', 4);
  assert.deepEqual(restored.decisions.slice(0,3), saved.decisions);
  const fresh = restored.decisions[3];
  assert.deepEqual([fresh.tag,fresh.description,fresh.staff_id,fresh.guideline_id,fresh.final],
    [freshTag,'Cross-role reauthenticated',2,1,'12.50']);
  await shot(staff, '10-fresh-submission');
  assert.equal(new Set(Object.values(evidence).map(s => s.db)).size, 1);
  await fs.writeFile(path.join(out,'sql-checkpoints.json'), JSON.stringify(evidence,null,2)+'\n');
});
