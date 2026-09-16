const express = require("express");
const path = require('path')
const Setting = require('../models/Setting');
const Notification = require('../models/Notification');
const router = express.Router();
const fetchuser = require('../middlewares/loggedIn');
const { uploadFile, getCurrentDate } = require("../utils");

let error = { status : false, message:'Something went wrong!' }
  
router.get('/stock-alert', fetchuser, async(req, res) => {
    try {
        const alert = await Setting.query().where('key', "STOCK_ALERT").where('user_id', req.body.myID ).first();
        if(alert) {
            return res.json({status:true, stock: JSON.parse(alert.value)});
        } 
    } catch (error) {
        return res.json({status:false, alert:0})
    }
})

router.post('/update-stock-alert', fetchuser, async (req,res) => {
    try {  
        const alert = await Setting.query().where('key', "STOCK_ALERT").where('user_id', req.body.myID ).first();
        if(alert) {
            await Setting.query().where('user_id', req.body.myID).where('key', 'STOCK_ALERT').patch({
                value: req.body.stock
            });
            return res.json({status:true, message:"Stock alert updated!"});
        } 
        await Setting.query().insert({
            user_id: req.body.myID,
            key: "STOCK_ALERT",
            value: req.body.stock  
        });

        return res.json({status:true, message:"Stock alert created!" });

    } catch (e) {
        console.log("exception occured: ",e)
        error.message = e.message
        return res.status(400).json(error)        
    }
});

router.get('/clear-notifications', fetchuser, async(req,res) => {
    try {
        await Notification.query().where('user_id', req.body.myID).delete();
        return res.json({status:true})
    } catch (error) {
        console.log(error.message);
        return res.json({status:false})
    }
})

router.get('/notifications', fetchuser, async(req,res)=> {
    try {
        return res.json({status:true, notifications: await Notification.query().where('user_id', req.body.myID)})
    }catch (err) {
        console.log(err.message)
        return res.json({status:false, notifications:[]})
    }
})

router.get('/notification/delete/:id', async(req,res) => {
    try {
        await Notification.query().where('id',req.params.id).delete();
        return res.json({status:true})
    } catch (error) {
        return res.json({status:false})
    }
})

router.get('/inventory/:state', fetchuser, async(req,res) => {
    try {
        const alert = await Setting.query().where('key', "INVENTORY_IS").where('user_id', req.body.myID ).first();
        if(alert) {
            await Setting.query().where('user_id', req.body.myID).where('key', 'INVENTORY_IS').patch({
                value: req.params.state
            });
            return res.json({status:true});
        } 
        await Setting.query().insert({
            user_id: req.body.myID,
            key: "INVENTORY_IS",
            value: req.params.state  
        });
        return res.json({status:true})
    } catch (error) {
        console.log(error.message)
        return res.json({status:false})
    }
})

router.get('/settings/:key', fetchuser, async( req, res ) => {
    return res.json({status:true, [req.params.key]: await Setting.query().where('user_id', req.body.myID).where('key',req.params.key).first('value')??''})
});

router.get(`/settings`, fetchuser, async(req, res) => {
    try {
        return res.json({
            status:true, 
            settings: await Setting.query().where('user_id', req.body.myID ).select('key', 'value') 
        })
    } catch (err) {
        res.json({status:false, settings:[]})
    }
})

router.get(`/upload-db/:client`, fetchuser, async(req, res) => {
    try {
        
        const filePath = path.join(__dirname, "../database/db.sqlite");
        const uploadURL = 'https://pos.dftech.in/upload-db'
        const uploaded = await uploadFile(filePath, uploadURL, req.params.client)
        if(uploaded) {

            const today = getCurrentDate('ymd')
            const last = await Setting.query().where('key', "LAST_UPDATED").where('user_id', req.body.myID ).first();
            if(last) {
                await Setting.query().where('user_id', req.body.myID).where('key', 'LAST_UPDATED').patch({
                    value: today
                });
                return res.json({status:true, settings: await Setting.query().where('user_id', req.body.myID ).select('key', 'value') });
            } else {
                await Setting.query().insert({
                    user_id: req.body.myID,
                    key: "LAST_UPDATED",
                    value: today  
                });
            }
            return res.json({
                status:true,
                settings: await Setting.query().where('user_id', req.body.myID ).select('key', 'value') 
            })
        }
        
        return res.json({
            status:false,
            message: "Failed to upload backup!",
            settings: await Setting.query().where('user_id', req.body.myID ).select('key', 'value') 
        })
    } catch (err) {
        console.log(err.message)
        res.json({status:false})
    }
})

module.exports = router