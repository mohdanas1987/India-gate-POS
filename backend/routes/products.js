const express = require("express");
const ProductCategory = require('../models/ProductCategory');
const Product = require('../models/Product');
const Tax = require('../models/Tax');
const XLSX = require('xlsx');
const axios = require('axios');
const sharp = require('sharp');
const url = require('url');
const path = require('path');
const router = express.Router();
const fs = require('fs');

const fetchuser = require('../middlewares/loggedIn');
const upload = require('../middlewares/multer');

const multer = require("multer");

const storage = multer.diskStorage({
    destination: function (req, file, cb) {
        const destPath = path.join(__dirname, "../tmp");
        req._dest = destPath
        cb(null, destPath);
    },
    filename: function (req, file, cb) {
        const uni = Date.now() + '-' + file.originalname;
        req._filename = uni
        cb(null, uni);
    },
});

const intjaam = multer({
    storage,
});

let error = { status : false, message:'Something went wrong!' }

const normalizeSpaces = (str) => str.replace(/\s+/g, ' ').trim()

router.get('/', async (req,res) => {
    try {
        let products;
        const cols = [
            'id',
            'name',
            'price',
            'code',
            'tax',
            'sales_desc',
            'image',
            'quantity',
            'pos',
            'category_id'
        ];

        if (req.body.category_id && req.body.category_id !== 'all') {
            products = await Product.query().where('category_id', req.body.category_id).where('deleted', false).orderBy('quantity', 'desc').select(cols).withGraphFetched().modifyGraph('category', (builder) => {
                builder.select(
                  'product_categories.name as catName'
                );
            });
        } else {
            products = await Product.query().where('deleted', false).orderBy('id', 'desc').select(cols).withGraphFetched('category').modifyGraph('category', (builder) => {
                builder.select(
                  'product_categories.name as catName'
                );
            });
        }
        return res.json({ status:true, products: products.map( item => {
            if(item.category) {
                item.catName = item.category.catName
                delete item.category
            }
            return item;
        }) })

    } catch (e) {
        error.message = e.message
        if (error.message.includes('column')) {
            await runCommand(`npx knex migrate:latest --cwd ${__dirname.replace('routes','')}`); 
            return { status: false, relaunch:true, message: "Module installed, please restart." };
        }
        return res.status(400).json(error);      
    }
});

router.post(`/updateStock/:id`, async (req, res)=> {
    try {
        const updated =  await Product.query().patchAndFetchById(req.params.id, {
            quantity: req.body.quantity
        });
        return res.json({status:true, message:'Stock updated!', product:updated }); 
    } catch (error) {
        return res.json({status:false, message:"Something went wrong!"});
    }
});

// Route 3 : Get logged in user details - login required
router.post('/create', [upload.single('image'), fetchuser ], async(req, res) =>{
    try { 

        if(req.body.barcode) {
            const existing = await Product.query().where('code',(req.body.barcode).trim()).where('deleted', false).first()
            if(existing) {
                return res.json({status:false, message:"This barcode already exists!"});
            }
        }
        const payload = {
            name: req.body.name,
            price: (req.body.price).trim()??0,
            code: (req.body.barcode).trim()?? null,
            category_id: req.body.category_id,   
        }
        const category = await ProductCategory.query().where('id', req.body.category_id).first();
        if(req.file)
        {
            const pName = payload.code ? payload.code: payload.name
            const updatedFilename = `${pName}.webp`;
            const outputPath = path.join(__dirname, '../tmp/products', updatedFilename.replace(/\s+/g, '_').trim());
    
            await sharp(req.file.path)
            .resize(400,400, {
                fit: 'inside'
            })
            .webp({ quality:50 })
            .toFile(outputPath);
            try { fs.unlinkSync(req.file.path) } catch (error) {}
            payload.image = `products/`+ updatedFilename.replace(/\s+/g, '_').trim();
        }

        const product = await Product.query().insertAndFetch(payload);

        return res.json({status:true, message:"Product added successfully!", product: category? {...product, catName: category.name}: product }); 

    } catch (e) {
        console.log(e.message)
        error.message = e.message
        return res.status(500).json(error)     
    }

}); 

router.post('/import', [ upload.single('file'), fetchuser ], async(req, res) => {
    try 
    { 
        const workbook = XLSX.readFile(req.file.path);
        const sheetName = workbook.SheetNames[0]; // Get the first sheet
        const sheetData = XLSX.utils.sheet_to_json(workbook.Sheets[sheetName]);

        for (const row of sheetData) {
 
            const catName = normalizeSpaces(row['Product Categories'])
            let category = await ProductCategory.query().findOne({ name: catName });

            if (!category) {
                category = await ProductCategory.query().insert({ name: catName });
            }

            // Step 2: Find or create the product with the barcode
            let path=null
            if(row.Images){
                path = await downloadAndProcessImage(row.Images)
                path = 'products/'+path;
            }

            let product = Product.query().where('code', row.Barcode).where('deleted',false).first()
            if(product) {
                await Product.query().insertGraph({
                    code: row.Barcode,
                    name: row.Name,
                    price: row['Sales Price'],
                    sales_desc: row['Sales Description'],
                    image: path,  // Use the uploaded path or the default image from Excel
                    category_id: category.id ?? null,  // Associate with the category
                    tax: row['Customer Taxes'] ?? null,  // Handle optional tax field
                });
            } else {
                await Product.query().findById(product.id).patch({
                    name: row.Name,
                    price: row['Sales Price'],
                    sales_desc: row['Sales Description'],
                    image: path,  // Use the uploaded path or the default image from Excel
                    category_id: category.id ?? null,  // Associate with the category
                    tax: row['Customer Taxes'] ?? null,  // Handle optional tax field
                });
            }
        }
        // res.status(200).send('Data successfully imported to the database!');
        return res.json({status:true, message: 'Products successfully imported!' }); 

    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 
 
router.post('/update', intjaam.single('uploaded'), async(req, res) =>{
    try {
        // if((req.body.catName)?.toLowerCase().indexOf('vege') && !req.body.code) return res.json({status:false, message:"Barcode can't be empty!"});
        if((req.body.catName)?.toLowerCase().indexOf('vege') && (req.body.catName)?.toLowerCase().indexOf('fruits') && !req.body.code) return res.json({status:false, message:"Barcode can't be empty!"});
        const product = await Product.query().where('code', (req.body.code).trim()).where('deleted', false).where('id','!=', req.body.id );
        if(req.body.code && product.length ) {
            return res.json({status:false, message:"This barcode already exists!"});
        }
        const body = {
            name: req.body.name,
            price:(req.body.price).trim(),
            tax: req.body.tax,
            code: (req.body.code).trim(),
        }
        if(req.file) {
            
            const pName = body.code ? body.code : Date.now()+ body.name;
            const updatedFilename = `${pName}.webp`;
            const outputPath = path.join(__dirname, '../tmp/products', updatedFilename.replace(/\s+/g, '_').trim());

            // Ensure file exists before processing
            if (!fs.existsSync(req._dest)) { // this done
                console.error("Error: Uploaded file does not exist.");
                return res.status(400).json({ error: "File not found" });
            }
        
            try {
                await sharp(req._dest + "/" + req._filename)
                    .resize(400, 400, { fit: 'inside' })
                    .webp({ quality: 50 })
                    .toFile(outputPath);
                    
                // Delete original file
                try {
                    fs.unlink(req._dest+'/'+req._filename, er => {
                        if(er) console.error("Error deleting original image",er);
                    })
                } catch (error) {}
                
                body.image = `products/` + updatedFilename.replace(/\s+/g, '_').trim();
                if(req.body.image!== 'null') {
                    const oldPath = path.join(__dirname, `../tmp/${req.body.image}`)
                    if (fs.existsSync(oldPath)) {
                        fs.unlink(oldPath, er => {
                            if(er) {
                                console.error("Error deleting old image",er);
                            }
                        })
                    }
                }

            } catch (error) {
                console.error("Sharp Processing Error:", error);
                return res.status(500).json({ error: "Image processing failed" });
            }
             
        }
        let toSync = {
            name: body.name,
            price: body.price,
            barcode: body.code,
            tax: body.tax
        }
        if(req.body.category_id) {
            body.category_id = req.body.category_id;
            toSync.category = req.body.catName
        }
        const {data} = await axios.post(`https://pos.dftech.in/products/update-product`, toSync, {
            headers: {
                'Authorization': req.body.appKey??'Vyo0WttjzBTh',
                'Content-Type': 'application/json'
            }
        })
        const updated = await Product.query().patchAndFetchById(req.body.id, body);
        return res.json({ status:true, updated, wasTrue: data.status });

    } catch (e) {
        console.log(e)
        error.message = e.message
        return res.json({...error, e})     
    }
}); 

router.get('/remove/:id', fetchuser, async(req, res) =>{
    try { 
        // return res.json({ status:true, removed:{} }); 
        const {id} = req.params
        const product = await Product.query().findById(id.split('_')[0]);
        try {
            if(product.image && product.image!='null') {
                fs.unlinkSync(path.join(__dirname, '../tmp/'+ product.image))
            }
        } catch (error) {throw new Error("Failed to remove the image: "+error.message)}
        const removed = await Product.query().patchAndFetchById(id.split('_')[0], {
            deleted: true
        });
        let disconnected=false;
        let rest
        try{
            let { data } = await axios.post(`https://pos.dftech.in/products/remove-product`, { product }, {
                headers: {
                    'Authorization': id.split('_')[1]??'Vyo0WttjzBTh',
                    'Content-Type': 'application/json'
                }
            })
            rest=data
        }catch(e){console.log(e);disconnected=true}
        if( removed ) {
            return res.json({ status:true, removed, message:'Product removed', data:rest, disconnected }); 
        } else {
            return res.json({
                status:false, 
                removed:'', 
                message:'Failed to remove!',
                data:{status:false}
            }); 
        }
    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 

async function downloadAndProcessImage(imageUrl) {
    try {
        const response = await axios({
            method: 'get',
            url: imageUrl,
            responseType: 'arraybuffer', // Get image data as a buffer
        });

        const imageBuffer = Buffer.from(response.data, 'binary');
        
        const parsed = url.parse(imageUrl);
        let fileName = path.basename(parsed.pathname);
        fileName = fileName.split('.')[0]+'.webp';
        // Use sharp to resize and reduce quality (e.g., 80% quality)
        await sharp(imageBuffer)
        .resize(400, 400, {
            fit: 'inside',
        })
        .webp({ quality: 35 })
        .toFile(path.join('tmp/products', fileName));
        // .resize(800)  // Resize width to 800px (optional)
        // .jpeg({ quality: 40 })  // Reduce quality to 80%
        // .toFile(path.join('tmp/products', fileName));  // Save the image locally
        return fileName;
    
    } catch (error) {
        console.error('Error downloading or processing the image:', error.message);
        return imageUrl;
    }

}

router.get(`/update-product-pos/:id/:status`, async(req, res)=> {
    try 
    {
        const {id,status} = req.params;
        await Product.query().findById(id).patch({
            pos: status
        });
        let product = await Product.query().where('id', id).select(['id','name','image','price','quantity','category_id','sales_desc','code']).first()
        const P = await ProductCategory.query().findById(product.category_id).select(['name']).first();
        return res.json({status:true, product:{...product, catName:P?.name??null} });

    } catch (error) {
        console.log(error.message);
        return res.json({ status:false, product:{} }) 
    }
});

router.get(`/barcode/:code`, async(req, res) => {
    try {
        const product = await Product.query().where('code', req.params.code).where('deleted',false).first();
        return res.json({ status:product.id?true:false, product });
    } catch (error) {
        return res.json({status:false, product:{}});   
    }
})

router.post('/convert', async(req, res) => {
    const resp = axios({
        method:"GET",
        url: req.body.image,
        responseType:'arraybuffer'
    });

    const imageBuffer = Buffer.from(resp.data, 'binary');
        

    let fileName = path.basename();
    fileName = fileName.split('.')[0]+'.webp';

    // Use sharp to resize and reduce quality (e.g., 80% quality)
    await sharp(imageBuffer)
    .resize(400, 400, {
        fit: 'inside',
    })
    .webp({ quality: 35 })
    .toFile(path.join('tmp/converted', fileName ));
    return res.json({status:true});
});

router.post(`/create-custom`, upload.single('image'), async(req,res) => {
    try {
        const payload = {
            name: req.body.name,
            price: (req.body.price).trim(),
            code: req.body.barcode,
            quantity: req.body.quantity ?? 3000,
            tax: req.body.tax ?? null
        }
        const existing = await Product.query().where('code', req.body.barcode).where('deleted',false).first();
        if(existing) {
            return res.json({
                status:false, 
                message:"A product with this barcode already exists!", 
                product:{}
            });
        }
        if(req.file){ 
 
            const {filename} = req.file;
            const updatedFilename = `${Date.now()}-${filename.slice(0,-5)}.webp`;
            const outputPath = path.join(__dirname+'../tmp/products', updatedFilename.replace(/\s+/g, '').trim());
    
            await sharp(req.file.path)
            .resize(400,400, { fit: 'inside' })
            .webp({ quality:50 })
            .toFile(outputPath);

            try { fs.unlinkSync(req.file.path); } catch (er) {}
            payload.image = `products/`+ updatedFilename.replace(/\s+/g, '').trim();
      
        }
        const product = await Product.query().insertAndFetch(payload);

        return res.json({
            status:true, 
            message:"Product has been added!", 
            product: {...product, taxAmount: product.price.replace(/\s+/g, '').trim() * parseFloat(product.tax.match(/\d+/g).join('')) / 100 }
        })
    } catch (error) {
        console.log(`exception while syncing: `+error.message)
        return res.json({status:false, message:error.message, product:{}})
    }
})

router.get('/sync/:key', fetchuser, async(req, res) => {
    try 
    { 
        const {data} = await axios.get(`https://pos.dftech.in/sync-products/${req.params.key}`)
        let products = data.original.split('\n');
        products = products.map( pr => {
            if(pr) pr = JSON.parse(pr)
                else pr = {}
            return pr
        })
        for (const line in products) {
            
            if (products[line]) { 
                const row = products[line];
                if(row && row.name && row.price){ // only start if it has price, name defined

                    let catName // for checking existence of category by name; to use its id as category_id
                    let category
                    let thisTax = row.tax.indexOf(' ')===-1 ? row.tax : (row.tax).split(' '); // its formatted as [num]% or ([num] %) also may contain [num]
                    
                    let tax = await Tax.query().where('amount', typeof thisTax==='object' ? thisTax[0]: thisTax).first()
                    let taxAmount = null; // this to be used in-place of row.tax (well 'row.tax' is already complete in itself)
                    if(!tax) { // if this type of tax doesnt exist create it.
                        let name="";
                        if(typeof thisTax==='object') {
                            name = thisTax[1]
                        }
                        await Tax.query().insert({
                            name,
                            amount:typeof thisTax==='object'? thisTax[0]: thisTax
                        })
                    }
                    if(row.category) { // check if the cateogory is defined, from here we'll get the category_id
                        catName = normalizeSpaces(row.category)
                        category = await ProductCategory.query().where('name', catName).first()
                        if (!category) {
                            category = await ProductCategory.query().insert({ name: catName })
                        }
                    }
                    if(row.barCode) { // if its not a vegetable-item
                        let product = await Product.query().where('code', row.barCode).where('deleted',false).first()
                        if(product) { // it is going to overwrite the existing price, make sure it is updated in phone as intended. (check update APIs)
                            await Product.query().findById(product.id).patch({
                                name: row.name,
                                price: (row.price.replace(/\s+/g, ''))?.replace(",", ".")??0,
                                category_id: category?.id??null,
                                quantity: 5000,
                                tax: row.tax
                            })
                        } else {
                            await Product.query().insertGraph({
                                code: row.barCode,
                                name: row.name,
                                price: (row.price.replace(/\s+/g, ''))?.replace(",", ".")??0,
                                category_id: category?.id ?? null,
                                tax: row.tax ?? null,  // Handle optional tax field
                                quantity: 5000
                            })
                        }
                    } else { // handle for vegetables
                        let product = await Product.query().where('name', row.name.trim()).where('deleted',false).first()  // check if the exact name matches
                        if(!product) {
                            await Product.query().insertGraph({
                                code: row.barCode??null,
                                name: row.name.trim(),
                                price: (row.price.replace(/\s+/g, ''))?.replace(",", ".")??0,
                                category_id: category?.id ?? null,
                                tax: row.tax ?? null,  // Handle optional tax field
                                quantity: 5000
                            });                
                        }
                    }

                }

            }
        }
        await axios.get(`https://pos.dftech.in/synced/${req.params.key}`);
        return res.json({ status:true, message: 'Products successfully imported!', products : products.map( pr => pr.length? JSON.parse(pr): pr)}); 

    } catch (e) {
        error.message = e.message
        console.log(e)
        return res.status(500).json(error)     
    }
}); 
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

module.exports=router 