const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
  const url=new URL(process.env.GW_ACCEPTANCE_ENDPOINT);
  assert.equal(url.hostname,'127.0.0.1');
  const browser=await chromium.launch({executablePath:process.env.GW_E2E_CHROME});
  try {
    const page=await browser.newPage();
    await page.goto(url.origin);
    await page.getByLabel('Email or Username',{exact:true}).fill('staff_e2e');
    await page.getByLabel('Password',{exact:true}).fill(process.env.GW_ACCEPTANCE_LOGIN);
    await page.getByRole('button',{name:'Login',exact:true}).click();
    if(process.argv[2]==='outage') {
      await page.getByText(/Cannot connect to the database/).waitFor();
      const text=await page.locator('body').innerText();
      assert.ok(!text.includes('fixture-db') && !text.includes(process.env.GW_ACCEPTANCE_LOGIN));
      assert.equal(await page.getByRole('button',{name:'Logout',exact:true}).count(),0);
    } else {
      await page.getByRole('button',{name:'History',exact:true}).click();
      await page.locator('.gw-activity-row').filter({hasText:process.env.GW_EXPECT_TAG}).waitFor();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});
