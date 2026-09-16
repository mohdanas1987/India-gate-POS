const express = require("express");
const Note = require('../models/Note');
const router = express.Router();
const multer = require('multer');
const path = require('path');
const fs = require('fs')

const fetchuser = require('../middlewares/loggedIn');
const sharp = require("sharp");

let error = { status : false, message:'Something went wrong!' }
const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, path.join(__dirname, '../tmp/temp')); // Directory where files will be stored
    },
    filename: (req, file, cb) => {
        cb(null, Date.now() + path.extname(file.originalname)); // Save file with unique name
    }
})

const upload = multer({
    storage: storage,
    limits: { fileSize: 2 * 1024 * 1024 }, // 2MB limit
    fileFilter: (req, file, cb) => {
      // Validate file type (optional)
      const fileTypes = /jpeg|jpg|png|webp/;
      const extname = fileTypes.test(path.extname(file.originalname).toLowerCase());
      const mimetype = fileTypes.test(file.mimetype);

      if (mimetype && extname) {
        return cb(null, true);
      }
      cb(new Error('Only jpeg|jpg|png|webp images are allowed!'));
    }
}).single('image');
  
router.get('/', async (req,res) => {
    try {  
        const notes = await Note.query();
        return res.json({status:true, notes });
    } catch (e) {
        console.log("exception occured: ",e)
        error.message = e.message
        return res.status(400).json(error)        
    }
});

// Route 3 : Get logged in user details - login required
router.post('/create', async(req, res) => {
    upload(req, res, async function (err) {
        if (err instanceof multer.MulterError) {
          return res.status(500).json({ error: 'File too large. Max size is 2MB.' });
        } else if (err) {
          return res.status(500).json({ error: err.message });
        }
    
        if (!req.file) {
          return res.status(500).json({ error: 'No file uploaded.' });
        }
    
        try {
          const { filename, path: tempPath } = req.file;
          const outputFile = path.join(__dirname, '../tmp/notes', filename);
    
          // Convert and save as webp
          await sharp(tempPath)
            .webp({ quality: 35 })
            .toFile(outputFile);
    
          // Delete original file
          try {
              fs.unlinkSync(tempPath);
          } catch (e) {}
    
          // Save note to DB
          const note = await Note.query().insert({
            amount: req.body.amount,
            image: "notes/" + filename,
            status: req.body.status ?? true,
          });
    
          return res.json({ status: true, note });
    
        } catch (e) {
          return res.status(500).json({ error: e.message });
        }
      });
}); 

router.get('/remove/:id', fetchuser, async(req, res) =>{
    try { 
        const note = await Note.query().findById(req.params.id);
        try {
            if(fs.existsSync(path.join(__dirname, '../tmp/'+note.image))){
                fs.unlinkSync(path.join(__dirname, '../tmp/'+note.image))
            }
        } catch (er) {}
        const noteDeleted = await Note.query().deleteById(req.params.id);
        if(noteDeleted) {
            return res.json({status:true, noteDeleted}); 
        } else {
            return res.json({status:false, noteDeleted}); 
        }
    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 

module.exports = router