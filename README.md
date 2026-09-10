# TexasHydro Database guide

    This is a repository for all data collected by the TexasHydro research group at the Bureau of Economic Geology, UT Austin.
The database is SQLite and meant to be used with the excel template data entry sheet in ./templates/submission_template.xlsx. To submit data, one fills out a copy of the template, and runs submit_data.bat. This will validate the data and add it to the database, more details are below.

## Submitting data quick guide

1. Go to ./templates/submission_template.xlsx
2. Fill out the fields that are relevant for your submission following the instructions on the template sheet
3. Save a copy into ./submissions/inbox/YOUR_FILE_NAME_HERE.xlsx
4. Go back to the ./texashydrodatabase
5. Double click submit_data.bat
6. Hopefully you are done and the database is updated!