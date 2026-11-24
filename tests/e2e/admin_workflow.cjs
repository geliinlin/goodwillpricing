const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { startApp } = require('./browser_fixture.cjs');
const out = process.env.GW_ACCEPTANCE_RESULTS || path.resolve(__dirname, '../../test-results/admin-workflow');

test('Administrator and staff share versioned pricing and historical evidence', {timeout: 180000}, async t => {
  const {base, browser, password} = await startApp(t);
  const admin = await browser.newPage({viewport:{width:1600,height:1100}});
  const staff = await browser.newPage({viewport:{width:1500,height:1080}});
  for (const p of [admin,staff]) p.setDefaultTimeout(25000);
  const btn = (p,name) => p.getByRole('button',{name,exact: typeof name === 'string'});
  const editor = admin.locator('.st-key-gw_guideline_editor_panel');
  async function idle(p) {
    await p.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    await p.locator('[data-stale=true]').first().waitFor({state:'detached'});
  }
  async function shot(p,name) { await idle(p); await p.screenshot({path:path.join(out,name+'.png')}); }
  async function choose(p,scope,label,value) {
    await idle(p);
    const input=scope.getByRole('combobox',{name:label,exact:true});
    await input.click(); await input.fill(value);
    await p.getByRole('option',{name:value,exact:true}).click();
    await p.getByRole('listbox').waitFor({state:'hidden'});
    assert.equal(await input.inputValue(),value);
  }
  async function login(p,username) {
    await p.goto(base);
    await p.getByLabel('Email or Username',{exact:true}).fill(username);
    await p.getByLabel('Password',{exact:true}).fill(password);
    await btn(p,'Login').click(); await btn(p,'Logout').waitFor();
  }
  async function selectLatest(id) {
    const canvas=admin.locator('[data-testid="stDataFrame"] .dvn-scroller').first();
    await idle(admin);
    await canvas.click({position:{x:16,y:55}});
    await editor.getByRole('heading',{name:'Update Guideline #'+id,exact:true}).waitFor();
  }
  async function save(price,id) {
    await editor.getByLabel('Suggested price ($)',{exact:true}).fill(price);
    if (id === 3) await btn(editor,'Save Guideline').dblclick();
    else await btn(editor,'Save Guideline').click();
    await admin.getByText('Guideline #'+id+' saved successfully.',{exact:true}).waitFor();
  }
  async function quote(price) {
    await btn(staff,'Search Guidelines').click();
    await staff.locator('.gw-suggestion .price').filter({hasText:price}).waitFor();
  }

  await login(admin,'admin_e2e@example.invalid');
  await shot(admin,'01-dashboard');
  await btn(admin,'Users').click(); await btn(admin,'+ Add New User').waitFor();
  await shot(admin,'02-users');
  await btn(admin,'+ Add New User').click();
  for (const [label,value] of [['First Name','Browser'],['Last Name','Fixture'],['Username','qa_browser'],['Email address','browser@example.invalid']]) {
    const input=admin.getByRole('textbox',{name:label,exact:true});
    await input.fill(value); await input.press('Enter');
  }
  await choose(admin,admin,'Branch','Test South');
  await admin.getByText('Active on creation',{exact:true}).click();
  await btn(admin,'Create User').click();
  await admin.getByText(/qa_browser.*created successfully/).waitFor();
  if (!process.env.GW_ACCEPTANCE_RESULTS) await shot(admin,'02a-created-account');
  await btn(admin,'Configuration').click(); await admin.getByText('Item Types',{exact:true}).first().waitFor();
  await shot(admin,'03-configuration');
  await admin.getByText('Branches',{exact:true}).click();
  await btn(admin,'+ Add Branch').click();
  for (const [label,value] of [['Name','QA browser branch'],['Street address','2 Test Rd'],['City','Fixture'],['State (2 letters)','MI'],['ZIP code','48800']])
    await admin.getByRole('textbox',{name:label,exact:true}).fill(value);
  await btn(admin,'Create').click();
  await admin.getByText('Branches entry #3 created.',{exact:true}).waitFor();
  await shot(admin,'03a-created-branch');
  await btn(admin,'Guidelines').click();
  await btn(admin,/Add New Guideline/).waitFor();
  await idle(admin);
  await btn(admin,/Add New Guideline/).click();
  const categoryWidget = editor.locator('[class*="st-key-gw_form_new_"][class*="_Category_Id_"]');
  const previousKey = await categoryWidget.evaluate(e => [...e.classList].find(c => c.startsWith('st-key-')));
  await choose(admin,editor,'Item type','Furniture');
  await editor.locator('.'+previousKey).waitFor({state:'detached'});
  await choose(admin,editor,'Category','Chairs');
  await save('15.00',3); await shot(admin,'04-created');
  await login(staff,'staff_e2e');
  await btn(staff,'Price an Item').click();
  for(const [field,val] of [['Item Type','Furniture'],['Category','Chairs'],['Brand Tier','Generic'],['Condition','Good']])
    await choose(staff,staff,field,val);
  await quote('$15.00'); await shot(staff,'05-staff-15');
  await selectLatest(3); await save('18.00',4);
  await quote('$18.00'); await shot(staff,'06-staff-18');
  await btn(staff,'Submit & Generate Tag').click();
  await staff.getByText('Tag generated successfully',{exact:true}).waitFor();
  const tag=await staff.locator('.gw-tag-id b').innerText();
  if (process.env.GW_ACCEPTANCE_RESULTS) {
    const downloading=staff.waitForEvent('download');
    await staff.getByText('Print Tag',{exact:true}).click();
    const file=await downloading;
    assert.equal(file.suggestedFilename(),'goodwill_'+tag+'.pdf');
    await file.saveAs(path.join(out,'release-tag.pdf'));
    await require('node:fs/promises').writeFile(path.join(out,'browser-tag.txt'),tag);
  }
  await selectLatest(4); await btn(editor,'Deactivate Guideline').click();
  await admin.getByText('Guideline #4 is now inactive.',{exact:true}).waitFor();
  await btn(staff,'Start Next Item').click();
  for(const [field,val] of [['Item Type','Furniture'],['Category','Chairs'],['Brand Tier','Generic'],['Condition','Good']])
    await choose(staff,staff,field,val);
  await btn(staff,'Search Guidelines').click(); await staff.getByText(/No active guideline matches/).waitFor();
  await shot(staff,'07-inactive');
  await btn(staff,'History').click();
  await staff.locator('.gw-activity-row').filter({hasText:tag}).filter({hasText:'Suggested $18.00'}).waitFor();
  await btn(admin,'Admin Dashboard').click(); await btn(admin,/Open Pricing History/).click();
  await admin.locator('.gw-activity-row').filter({hasText:tag}).filter({hasText:'Suggested $18.00'}).waitFor();
  await shot(admin,'08-history');
  await btn(admin,'Reports').click();
  const exporting=admin.waitForEvent('download');
  await admin.getByText('Export to PDF',{exact:true}).click();
  await (await exporting).saveAs(path.join(out,'report-export.pdf'));
  await shot(admin,'09-reports');
  const expected=process.env.GW_ACCEPTANCE_METRICS ? JSON.parse(process.env.GW_ACCEPTANCE_METRICS) : ['2','$15.25','0.0%','2'];
  assert.deepEqual(await admin.locator('.gw-report-metric-value').allInnerTexts(), expected);
  await btn(admin,'Logout').click(); await btn(admin,'Login').waitFor();
});
