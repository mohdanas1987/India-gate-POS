const express = require("express");
const User = require('../models/user');
const Product = require('../models/Product');
const Currency = require('../models/Currency');
const router = express.Router();
const { body, validationResult }=require('express-validator')
const bcrypt= require('bcrypt');
const jwt= require('jsonwebtoken');
const fetchuser= require('../middlewares/loggedIn');
const JWT_SECRET = 'whateverItWas';

let error = { status : false, message:'Something went wrong!' }
let output = { status : true }

// Create a user
router.post('/signup',[
    body('name').isLength({min:5}),
    body('username').isLength({min:5}),
    body('password').isLength({min:6}),
],async (req,res) => {
    try 
    {
        const errors=validationResult(req);
         
        if(!errors.isEmpty()) return res.status(400).res.json({errors : errors.array()});
         
        let user = await User.query().where('email', req.body.email);
        if(user){
            return res.status(400).json({...error, key:'email', message:'A user with that email already exists!'})
        } 
        const salt = await bcrypt.genSalt(8);
        const secPass = await bcrypt.hash(req.body.password, salt)
        // Finally create one
        user = User.query().insert({
            name: req.body.name,
            email : req.body.email.toLowerCase(),
            password : secPass
        });

        output.message = 'Account created successfully!'
        return res.json(output);
        
    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)        
    }
});

// Route 3 : Authenticate the user
router.post('/login',[ 
    body('email','Invalid credentials!').isLength({min:5}),
    body('password','Password cannot be blank!').exists(),
],async (req,res) => {
    try {
        const errors=validationResult(req);
        // Iff ? its finished : can go
        if(!errors.isEmpty()){
            return res.status(400).json({errors : errors.array()})
        } 
        let user = await User.query().where('email', req.body.email).first();
        if(!user){
            error.message = "User not found, please create an account to start!"
            return res.status(400).json(error)
        }

        const compared = await bcrypt.compare(req.body.password, user.password)
        if(!compared){
            error.message = "Incorrect Password!"
            return res.status(400).json(error)
        }
        let currency = await Currency.query().where('status', true).first();
        if(currency) {
            currency = (currency.name).toLowerCase() == 'euro'? '€ ': '₹ ';
        }
        
        const payload = {
            user : {
                id : user.id
            }
        }
        const authToken = jwt.sign(payload, JWT_SECRET);
        return res.json({status:true, authToken, user, currency});
        
    } catch (e) {
        console.log("exception occured: ",e)
        error.message = e.message
        return res.status(400).json(error)        
    }
});

// Route 3 : Get logged in user details - login required
router.get('/getuser', fetchuser, async(req, res) =>{
    try {
        const userid = req.body.id; 
        const user = await User.query().findById(userid);
        return res.json(user) 
    } catch (e) {
        error.message = e.message
        return res.status(500).json(error)     
    }
    }
);

router.get("/seed", async(req,res)=> {
    // const salt = await bcrypt.genSalt(8);
    // const password = await bcrypt.hash('121212', salt);
    // await Product.query().truncate();
    // const created = await Currency.query().insert({ name:'euro', status:true })
    return res.json({msg:'cleared'});
})

module.exports=router 