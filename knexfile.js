// Update with your config settings.

/**
 * @type { Object.<string, import("knex").Knex.Config> }
 */
module.exports = {

  development: {
    client: 'sqlite3',
    connection: {
      filename: './database/db.sqlite'
    },
    migrations: {
      tableName: 'knex_migrations'
    }
  },

  staging: {
    client: 'sqlite',
    connection: {
      filename: './database/db.sqlite'
    },
  },

  production: {
    client: 'sqlite',
    connection: {
      filename: './database/db.sqlite'
    },
   
  }

};
