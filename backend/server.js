const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const cors = require('cors');
const { Model } = require('objection');
const Knex = require('knex');
const path = require('path');

const knex = Knex({
  client: 'sqlite3',
  connection: {
    filename: path.join(__dirname, 'database/db.sqlite'),
  },
  useNullAsDefault: true,
});

Model.knex(knex);

const app = express();
const port = 5100;
const buildPath = path.join(__dirname, 'client/build');
app.use(cors());
app.use(express.json());
app.use('/images', express.static(path.join(__dirname, 'tmp')));
app.use(express.static(buildPath))

const db = new sqlite3.Database('./database/db.sqlite', (err) => {
    if (err) {
        console.error('Error opening database', err.message);
    } else {
        console.log('Connected to SQLite database');
    }
});

app.use("/auth", require("./routes/auth"));
app.use("/products", require("./routes/products"));
app.use("/orders", require("./routes/orders"));
app.use("/category", require("./routes/category"));
app.use("/tax", require("./routes/tax"));
app.use("/pos", require("./routes/pos"));
app.use("/notes", require("./routes/notes"));
app.use("/config", require("./routes/config"));

app.get('/install-update', async(req, res)=> {
    try {
        const fs = require('fs');
        const axios = require('axios')
        const url = 'https://pos.dftech.in/updates/download';
        const outputFolder = path.join( __dirname, './tmp' );
        const outputPath = path.join( outputFolder, 'update.zip' );
        // Download and save file
        const {data} = await axios({ method: 'GET', url, responseType: 'stream' })
        const writer = fs.createWriteStream(outputPath)
        data.pipe(writer)
        writer.on('finish', async () => {
            const data = await extractZip(outputPath, path.join(__dirname,'client'))
            fs.unlinkSync(outputPath)
            if(!data.status) {
                return res.json(data);
            }
            return res.json({status:true, message: 'UI update installed!'});
        })
        writer.on('error', () => {
            return res.json({status:false, message: 'Failed downloading update!'});
        })

    } catch (error) {
        return res.json({status:false, message: error.message});
    }
})

app.get(`/install-backend-update`, async(req,res) => {

    let extractPath
    try {
        const fs = require('fs')
        const axios = require('axios')
        const url = 'https://pos.dftech.in/backend-updates/download'
        const outputFolder = path.join(__dirname, './tmp')
        const outputPath = path.join(outputFolder, 'main.zip')

        const {data} = await axios({
            method: 'GET',
            url,
            responseType: 'stream',
        })
        const writer = fs.createWriteStream(outputPath);
        data.pipe(writer);
        writer.on('finish', async() => {
            const data = await extractZip(outputPath, path.join( __dirname, '../'))
            fs.unlinkSync(outputPath)
            if(!data.status) {
                return res.json(data);
            }
            return res.json({ status:true, message:"UX Updates installed!"})
        });
        writer.on('error', err => {
            return res.json({status:false, message:"Updates installation failed!", fileWritingReason: err.message })
        });

    } catch (er) {
        return res.json({ status:false, message: er.message, pathAtouter:extractPath, cpath: __dirname })
    }
})

async function extractZip(source, destination) {
    try {
        const AdmZip = require('adm-zip');
        const zip = new AdmZip(source);
        zip.extractAllTo(destination, true); 
        return { status: true, message: "Update finished!" };
    } catch (error) {
        if (error.message.includes('Cannot find module')) {
            await runCommand(`npm install --prefix ${__dirname} adm-zip`); 
            return { status: false, relaunch:true, message: "Module installed, please restart." };
        }
        return { status: false, message: error.message };
    }
}


async function runCommand(command) {
    const { exec } = require('child_process');
    const util = require('util');
    const execPromise = util.promisify(exec);
    try {
        const { stdout, stderr } = await execPromise(command);
        if (stderr) {
            return {output:`⚠️ Command executed with warnings: ${stderr}`};
        }        
        return {output:`✅ Command successful:\n${stdout}`};

    } catch (error) { 
        return {output:`❌ Command failed: ${error.message}`};
    }
}

let server
function start(){
  server = app.listen(port)
  server.on("error", (err) => {
      if (err.code === "EADDRINUSE") {
        console.error(`❌ Port ${port} is already in use.`);
        process.exit(1); // Exit the process
      } else {
        console.error("Server error:", err);
      }
});
}

function stop(){
  server.close()
}

module.exports = {start, stop }