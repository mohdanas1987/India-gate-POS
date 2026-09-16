const fs = require('fs');
const path = require('path');

const storage = {
  /**
   * Save a file to the specified path
   * @param {string} filePath - The relative path to save the file (e.g., "reports/file.pdf")
   * @param {Buffer|string} content - The file content (Buffer for binary files, string for text)
   * @returns {Promise<void>}
   */
  async put(filePath, content) {
    try {
      const fullPath = path.join(__dirname, '../tmp', filePath); // Save inside a "storage" folder
      // Ensure the directory exists
      await fs.promises.mkdir(path.dirname(fullPath), { recursive: true });
      // Write the file
      await fs.promises.writeFile(fullPath, content);
    } catch (err) {
      console.error('Error saving file:', err);
      throw new Error('Failed to save file');
    }
  },

  /**
   * Read a file from the specified path
   * @param {string} filePath - The relative path to the file (e.g., "reports/file.pdf")
   * @returns {Promise<Buffer>} - Returns the file content as a Buffer
   */
  async get(filePath) {
    try {
      const fullPath = path.join(__dirname, '../tmp', filePath);
      return await fs.promises.readFile(fullPath);
    } catch (err) {
      console.error('Error reading file:', err);
      throw new Error('Failed to read file');
    }
  },

  /**
   * Delete a file from the specified path
   * @param {string} filePath - The relative path to the file (e.g., "reports/file.pdf")
   * @returns {Promise<void>}
   */
  async delete(filePath) {
    try {
      const fullPath = path.join(__dirname, '../tmp', filePath);

      // Check if the file exists before attempting to delete
      if (fs.existsSync(fullPath)) {
        await fs.promises.unlink(fullPath);
      } else {
        console.warn('File not found:', fullPath);
      }
    } catch (err) {
      console.error('Error deleting file:', err);
      throw new Error('Failed to delete file');
    }
  },

  /**
   * Check if a file exists at the specified path
   * @param {string} filePath - The relative path to the file (e.g., "reports/file.pdf")
   * @returns {Promise<boolean>} - Returns true if the file exists, false otherwise
   */
  async exists(filePath) {
    try {
      const fullPath = path.join(__dirname, '../tmp', filePath);
      return fs.existsSync(fullPath);
    } catch (err) {
      console.error('Error checking file existence:', err);
      throw new Error('Failed to check file existence');
    }
  }
};

module.exports = storage;