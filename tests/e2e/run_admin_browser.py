"""Run the cross-role browser scenario on its own disposable fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys

import mysql.connector
from mariadb_fixture import fixture

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / 'test-results/admin-workflow'

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    with fixture() as (config, directory), mysql.connector.connect(**config) as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO USER_MODULE_ACCESS (User_Id,Can_Manage_Users_Config) VALUES(1,1)')
            conn.commit()
        subprocess.run(['node','--test',str(HERE/'admin_workflow.cjs')], check=True,
                       env={**os.environ,'GW_E2E_DIR':str(directory),'GW_E2E_PYTHON':sys.executable})
        with conn.cursor() as cur:
            cur.execute("""SELECT Guideline_Id,Guideline_Suggested_Price,Guideline_Is_Active
                           FROM PRICING_GUIDELINE WHERE Guideline_Id>2 ORDER BY Guideline_Id""")
            versions = [(i,str(p),a) for i,p,a in cur.fetchall()]
            assert versions == [(3,'15.00',0),(4,'18.00',0)], versions
            cur.execute("""SELECT d.Guideline_Id,d.Staff_Id,g.Guideline_Suggested_Price,
                           d.Decision_Final_Price,d.Decision_Tag_Number FROM PRICING_DECISION d
                           JOIN PRICING_GUIDELINE g ON d.Guideline_Id=g.Guideline_Id ORDER BY d.Decision_Id""")
            history=cur.fetchall()
            assert [(i,s,str(p),str(f)) for i,s,p,f,_ in history] == [(1,3,'12.50','12.50'),(4,2,'18.00','18.00')]
            cur.execute("SELECT u.User_Is_Active,s.Branch_Id FROM USER_ACCOUNT u JOIN STORE_STAFF s ON u.User_Id=s.Staff_Id WHERE u.User_Username='qa_browser'")
            account=cur.fetchall()
            assert account == [(0,2)], account
            cur.execute("SELECT Branch_Name,Branch_Is_Active FROM BRANCH WHERE Branch_Id=3")
            branch=cur.fetchall()
            assert branch == [('QA browser branch',1)], branch
            cur.execute('''SELECT c.Category_Name, CONCAT(u.User_First_Name,' ',u.User_Last_Name),
                                  b.Branch_Name,d.Decision_Final_Price
                           FROM PRICING_DECISION d JOIN ITEM i USING(Item_Id)
                           JOIN CATEGORY c USING(Category_Id) JOIN STORE_STAFF s USING(Staff_Id)
                           JOIN USER_ACCOUNT u ON u.User_Id=s.Staff_Id LEFT JOIN BRANCH b ON b.Branch_Id=d.Decision_Branch_Id
                           ORDER BY d.Decision_Id''')
            export_rows = cur.fetchall()
            cur.execute('SELECT VERSION()')
            (OUT/'browser-sql.json').write_text(json.dumps(dict(server=cur.fetchone()[0],
                versions=versions,decisions=history,created_staff=account,created_branch=branch,export_rows=export_rows),default=str,indent=2)+'\n')
