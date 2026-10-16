// In-memory PostgreSQL only; no DSN, server, credentials, or persistent path.
import {pathToFileURL} from 'node:url';
import readline from 'node:readline';
const {PGlite} = await import(pathToFileURL(process.argv[2]).href);
const db = new PGlite();
await db.waitReady;
process.stdout.write(JSON.stringify({ready:true})+'\n');
for await (const line of readline.createInterface({input:process.stdin})) {
  try {
    const request=JSON.parse(line);
    const result=await db.query(request.sql,request.parameters||[]);
    process.stdout.write(JSON.stringify({rows:result.rows})+'\n');
  } catch(error) {
    process.stdout.write(JSON.stringify({error:String(error.message)})+'\n');
  }
}
await db.close();
