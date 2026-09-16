/**
 * @param { import("knex").Knex } knex
 * @returns { Promise<void> } 
 */
exports.seed = async function(knex) {
  // Deletes ALL existing entries
  await knex('currency').del()
  await knex('currency').insert([
    {name :'INR', status:false },
    {name :'Euro', status:true },
  ]);
};
