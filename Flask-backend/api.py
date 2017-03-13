#!/usr/bin/env python3
import os
from flask import Flask, abort, request, jsonify, g, url_for, send_from_directory
from flask_cors import CORS, cross_origin
from flask_sqlalchemy import SQLAlchemy
from flask_httpauth import HTTPBasicAuth
from passlib.apps import custom_app_context as pwd_context
from itsdangerous import (TimedJSONWebSignatureSerializer as Serializer, BadSignature, SignatureExpired)
from werkzeug.utils import secure_filename
import uuid

# Database login information -- uses pymysql as connector --
# 'mysql+pymysql://user:password@host/database'
DATABASE_LOGIN_STRING = 'mysql+pymysql://root:password@localhost/textbook_town'

SERVER = 'http://127.0.0.1:5000/'
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])     # allowed file extensions
UPLOAD_FOLDER = os.path.abspath('./img')                    # folder to store uploaded files
TOKEN_EXPIRATION = 604800

# Initialize our Flask app
app = Flask(__name__, static_folder=os.path.abspath('./img'))
app.config['SECRET_KEY'] = 'the quick brown fox jumps over the lazy dog'
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_LOGIN_STRING
app.config['SQLALCHEMY_COMMIT_ON_TEARDOWN'] = True
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Initialize database and authentication extension objects
db = SQLAlchemy(app)
CORS(app)
auth = HTTPBasicAuth()

# import helper functions from validate.py
from validate import validPubYear, stringToDate, validDateString, validMinimumBid


###### DEFINE THE DATA MODELS ######
class User(db.Model):
    '''
    Models user
    '''
    __tablename__ = 'users'
    # Columns in the users table
    id = db.Column(db.Integer, primary_key=True)            # id
    username = db.Column(db.String(32), index=True)         # username
    password_hash = db.Column(db.Text)                      # hashed password stored instead of plain text
    contact = db.Column(db.Text)                            # contact method

    # REQUIRED FUNCTIONS FOR HTTP BASIC AUTH EXTENSION
    def hash_password(self, password):
        self.password_hash = pwd_context.encrypt(password)

    def verify_password(self, password):
        return pwd_context.verify(password, self.password_hash)

    def generate_auth_token(self, expiration=TOKEN_EXPIRATION):
        s = Serializer(app.config['SECRET_KEY'], expires_in=expiration)
        return s.dumps({'id': self.id})

    @staticmethod
    def verify_auth_token(token):
        s = Serializer(app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except SignatureExpired:
            return None    # valid token, but expired
        except BadSignature:
            return None    # invalid token
        user = User.query.get(data['id'])
        return user


class Textbook(db.Model):
    '''
    Models Textbook object to be stored in the database
    '''
    __tablename__ = 'textbooks'
    id = db.Column(db.Integer, primary_key=True)    # id: primary key
    title = db.Column(db.Text)                      # title
    author = db.Column(db.Text)                     # author
    isbn = db.Column(db.Text)                       # isbn
    publisher = db.Column(db.Text)                  # publisher
    yearPublished = db.Column(db.Integer)           # year published
    description = db.Column(db.Text)                # description of book
    version = db.Column(db.Text)                    # version
    condition = db.Column(db.Integer)               # 0-100 rating of textbook quality
    course = db.Column(db.Text)                     # course this book is used in
    coverPhotoName = db.Column(db.String(50))       # filename of cover photo, stored in ./img/
    bestPhotoName = db.Column(db.String(50))        # filename of best photo, stored in ./img/
    worstPhotoName = db.Column(db.String(50))       # filename of worst photo, stored in ./img/
    averagePhotoName = db.Column(db.String(50))     # filename of worst photo, stored in ./img/
    seller = db.Column(db.Integer)                  # id of the seller
    auction = db.Column(db.Integer)                 # id of the auction


class Bid(db.Model):
    '''
    Models Bid object to be stored in "bids" table in database
    '''
    __tablename__ = 'bids'
    id = db.Column(db.Integer, primary_key=True)    # id
    ceiling = db.Column(db.Integer)                 # bid ceiling
    bidder = db.Column(db.Integer)                  # id of bidder (User) to whom this bid belongs
    auction = db.Column(db.Integer)                 # id of Auction to which this bid belongs


class Auction(db.Model):
    '''
    Models Auction object to be stored in "auctions" table in database
    '''
    __tablename__ = 'auctions'
    id = db.Column(db.Integer, primary_key=True)
    textbook = db.Column(db.Integer)                 # id of the textbook for this auction
    minimumBid = db.Column(db.Integer)               # minimum bid
    salePrice = db.Column(db.Integer)                # sale price, updated each time a bid comes in, starts at 0 if no bids
    isCurrent = db.Column(db.Boolean)                # whether or not auction is open
    closingDate = db.Column(db.Date)                 # closing date (auction ends at midnight ET of this day)


###### APP ROUTES (API ENDPOINTS) ######
@auth.verify_password
def verify_password(username_or_token, password):
    '''
    Helper function to verify password
    :param username_or_token: either username or token used for auth
    :param password: password if username is sent, otherwise can be blank if token is used instead of username
    :return: True or False if valid username:password or valid token:
    '''
    # first try to authenticate by token
    user = User.verify_auth_token(username_or_token)
    if not user:
        # try to authenticate with username/password
        user = User.query.filter_by(username=username_or_token).first()
        if not user or not user.verify_password(password):
            return False
    # Set Flask global object g with a property of the current user so other methods can check the current user
    g.user = user
    return True


@app.route('/user/register', methods=['POST'])
def new_user():
    '''
    User account signup backend endpoint at SERVER/user/register
    :return: {'status': 'success'} or {'status': 'failure', 'message': 'message'} or 400 if missing arguments
    '''
    username = request.json.get('username').lower()
    password1 = request.json.get('password')
    password2 = request.json.get('passwordCheck')
    contact = request.json.get('contactLink')

    # VALIDATE INPUT
    if username is None or password1 is None or password2 is None or contact is None:
        return jsonify({'status': 'failure', 'message': 'missing_arguments'})

    if contact=="":
        return jsonify({'status': 'failure', 'message': 'missing_arguments'})

    # verify username isn't too short or too long
    if len(username) > 32:
        return jsonify({'status': 'failure', 'message': 'username_too_long'})
    if len(username) < 4:
        return jsonify({'status': 'failure', 'message': 'username_too_short'})

    # passwords don't match
    if password1 != password2:
        return jsonify({'status': 'failure', 'message': 'passwords_not_matching'})
    # password too short
    if len(password1) < 6:
        return jsonify({'status': 'failure', 'message': 'password_too_short'})

    # already a user with that name
    if User.query.filter_by(username=username).first() is not None:
        # return failure message
        return jsonify({'status': 'failure', 'message': 'username_taken'})

    # make new User and add to the database
    user = User(username=username, contact=contact)
    user.hash_password(password1)
    db.session.add(user)
    db.session.commit()

    # return success json
    return jsonify({'status': 'success'})


@app.route('/user/login')
@auth.login_required
def get_auth_token():
    '''
    If valid username:password provided, returns json with status, token, and token duration at SERVER/user/login
    If invalid, returns 401 with "Unauthorized Access" in the body
    :return:
    '''
    token = g.user.generate_auth_token(TOKEN_EXPIRATION)
    return jsonify({'status': 'success', 'token': token.decode('ascii'), 'duration': TOKEN_EXPIRATION})


@app.route('/api/users/<int:id>')
def get_user(id):
    '''
    Helper function, not sure exactly what it's used for
    '''
    user = User.query.get(id)
    if not user:
        abort(400)
    return jsonify({'username': user.username})


@app.route('/img/<path:filename>')
def serve_file(filename):
    '''
    Serve static files to front end at SERVER/img/filename
    :param filename:
    :return:
    '''
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)


@app.route('/book/add', methods=['POST'])
@auth.login_required
def add_book():
    '''
    Server-side functionality for adding a textbook
    :return: JSON {'status': 'success', 'id': newBook.id} or {'status': 'failure'}
    '''

    cover = request.files['cover']
    best = request.files['pic1']
    worst = request.files['pic2']
    average = request.files['pic3']

    # if any of the files are missing or have disallowed extensions
    if not allowedFile(cover.filename) or not allowedFile(best.filename) or not allowedFile(worst.filename) or not allowedFile(average.filename):
        return jsonify({'status': 'failure', 'message': 'Missing photos or improper photo extensions'})

    # Now we know files are good to go!

    # get the form data (excluding files)
    form = request.form.to_dict()              # get the form data (excluding files)

    # get stuff from the form
    title = form['title']
    isbn = form['isbn']
    author = form['author']
    publisher = form['publisher']
    version = form['version']
    minimumBidStr = form['price']
    course = form['subject']
    pubYearStr = form['year']
    desc = form['description']
    ratingStr = form['rating']
    dateStr = form['sellby']

    # if any of these fields are blank, return blank_fields json
    if title=="" or isbn=="" or author=="" or publisher=="" or version=="" or minimumBidStr=="" or course=="" \
    or pubYearStr=="" or ratingStr=="" or minimumBidStr=="" or dateStr=="":
        return jsonify({'status': 'failure', 'message': 'All form fields must be filled out'})

    # validate year published
    if validPubYear(pubYearStr):
        pubYear = int(pubYearStr)
    else:
        return jsonify({'status': 'failure', 'message': 'Year published must be between 1900 and 2017'})

    # validate closingDate
    if validDateString(dateStr):
        closingDate = stringToDate(dateStr)
    else:
        return jsonify({'status': 'failure', 'message': 'Auction must close between tomorrow and 60 days from now'})

    rating = int(ratingStr)

    # validate minimumBid
    if validMinimumBid(minimumBidStr):
        minimumBid = int(minimumBidStr)
    else:
        return jsonify({'status': 'failure', 'message': 'Minimum bid must be a positive integer'})

    # Yay now we know everything is valid!
    # CREATE UNIQUE FILENAMES AND STORE
    coverPath = uniqueFileName(cover.filename)
    cover.save(os.path.join(app.config['UPLOAD_FOLDER'], coverPath))
    bestPath = uniqueFileName(best.filename)
    best.save(os.path.join(app.config['UPLOAD_FOLDER'], bestPath))
    worstPath = uniqueFileName(worst.filename)
    worst.save(os.path.join(app.config['UPLOAD_FOLDER'], worstPath))
    averagePath = uniqueFileName(average.filename)
    average.save(os.path.join(app.config['UPLOAD_FOLDER'], averagePath))

    # get current logged in user who is selling book
    seller = g.user.id

    # Create Textbook object with all info except auction id
    newBook = Textbook(title=title, author=author, isbn=isbn, publisher=publisher, description=desc, version=version,
                       condition=rating, course=course, coverPhotoName=coverPath, bestPhotoName=bestPath,
                       worstPhotoName=worstPath, averagePhotoName=averagePath, seller=seller, yearPublished=pubYear)

    # Create Auction object with all info except textbook id
    newAuction = Auction(minimumBid=minimumBid, salePrice=0, isCurrent=True, closingDate=closingDate)

    # add our new objects to database
    db.session.add(newAuction)
    db.session.add(newBook)
    # flush to update db so we can get their ids
    db.session.flush()

    # update auction id of textbook and textbook id of auction and commit changes
    newBook.auction = newAuction.id
    newAuction.textbook = newBook.id
    db.session.commit()

    # return successful response
    return jsonify({'status': 'success', 'id': newBook.id})


@app.route('/login/check', methods=['POST'])
def valid_token():
    '''
    For frontend to check if user is logged in or not by checking token validity
    :return: {'status': 'success'} if logged in or {'status': 'failure'} if not
    '''
    # get token from POST json body
    token = request.json.get('token')
    # get user with that token, if there is one
    user = User.verify_auth_token(token)
    if user is None:
        return jsonify({'status': 'failure'})
    return jsonify({'status': 'success'})


###### TEST ENDPOINTS FOR PRACTICE #######

# Testing login required endpoint
@app.route('/api/json', methods=['POST'])
@auth.login_required
def get_json():
    print('thing' in request.json)
    print(g.user.username)
    thing = request.json.get('thing')
    return jsonify({'thing': thing})


###### HELPER METHODS FOR APP ROUTES ######
# Can't seem to put these in a separate file, getting some weird circular imports or something so I left them
def allowedFile(filename):
    '''
    Determines if file has a valid extension
    :param filename: original name of uploaded file
    :return: True if file extension is valid, False if not valid
    '''
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def uniqueFileName(filename):
    '''
    Generates a unique filename for an image
    :param filename: String of original filename
    :return: new filename (uuid + extension)
    '''

    # make new filename with uuid + extension
    filename = secure_filename(str(uuid.uuid4()) + filename[-4:])
    # uuid is EXTREMELY unlikely to have duplicates, but make sure to be safe
    while os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        filename = secure_filename(str(uuid.uuid4()) + filename[-4:])
    return filename


if __name__ == '__main__':
    app.run(debug=True)