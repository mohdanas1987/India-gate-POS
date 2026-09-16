const express = require("express");
const Tax = require('../models/Tax');
// const Currency = require('../models/Currency');
const router = express.Router();

const fetchuser= require('../middlewares/loggedIn');

let error = { status : false, message:'Something went wrong!' }
let output = { status : true }
  
router.get('/', async (req,res) => {
    try {  
        let taxes = await Tax.query().where('status', true);
        return res.json({status:true, taxes });
    } catch (e) {
        console.log("exception occured: ",e)
        error.message = e.message
        return res.status(400).json(error)        
    }
});

router.get('/list', async (req,res) => {
    try {  
        let taxes = await Tax.query();
        return res.json({status:true, taxes });
    } catch (e) {
        console.log("exception occured: ",e)
        error.message = e.message
        return res.status(400).json(error)        
    }
});

// Route 3 : Get logged in user details - login required
router.post('/create', fetchuser, async(req, res) =>{
    try { 

        const tax = await Tax.query().insert({
            name: req.body.name,
            amount: req.body.amount??0,
            status: req.body.status??true,
        });

        return res.json({status:true, tax }); 

    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 
 
router.post('/update', fetchuser, async(req, res) =>{
    try { 

        const tax = await Tax.query().patchAndFetchById(req.body.id, {
            name: req.body.name,
            amount:req.body.amount,
            status: req.body.status
        });

        return res.json({status:true, tax }); 

    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 

router.get('/remove/:id', fetchuser, async(req, res) =>{
    try { 
        const taxDeleted = await Tax.query().deleteById(req.params.id);
        if(taxDeleted) {
            return res.json({status:true, taxDeleted}); 
        } else {
            return res.json({status:false, taxDeleted}); 
        }
    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
}); 

router.get('/toggle/:id/:status', async (req,res) => {
    try { 
        const tax = await Tax.query().patchAndFetchById(req.params.id, {
            status: req.params.status
        }); 
        return res.json({status:true, tax, message: "Status updated!" });
    } catch (error) {
        return res.json({ status:false, tax:{}, message: error.message, message: "Something went wrong!" })   
    }
})

module.exports=router 